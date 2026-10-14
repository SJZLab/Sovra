#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,assignment,call-arg,index,var-annotated,misc"
"""FTF-0503 -- the JSON importer over PostgreSQL 16 as the restricted `sovra_app` role and
the live SeaweedFS S3 API, through the real FTF-0501 upload routes and the FTF-0502-P1
dataset source reader.

`tests/tasks/test_ftf_0503.py` proves the importer on in-memory bytes. This suite proves
what only the real services can:

J1  a JSON document uploaded through the real FTF-0501 routes (create -> presigned PUT ->
    finalize, declared and server-verified `application/json`) is streamed back from the
    real bucket by `open_dataset_source` as `sovra_app` and imported by `run_json_import`
    with the dataset's own verified content type: a leading BOM, CRLF whitespace, an ignored
    `_meta` sibling, Arabic text, leading-zero identifiers, formula text and escaped line
    separators are all handled, every good record is preserved exactly, and the bad elements
    (invalid JSON token, a non-object, a duplicate key, NaN, a wrong-typed slot, a missing
    slot) each get their own code and `record:<n>` locator, so `records_seen == accepted +
    rejected` and no element is lost; the report's source hash is `Dataset.verified_sha256`;
    the same result for two chunkings of the live stream; the root-array (`$`) and one-record
    (`$record`) containers import from real storage too
J2  a dataset whose verified content type is not `application/json` (a CSV, a JSONL file) is
    refused by the importer with `content_type_not_supported_by_importer` before one byte is
    requested from storage (the counting client sees no call)
J3  a foreign organization's dataset, a missing id and a hidden held-out dataset all raise
    the same `DatasetNotFoundError` (so the importer is never reached); held-out is
    readable only with include_held_out=True and then imports
J4  structure problems in stored documents end the import `failed` with their own code, the
    byte offset and the mapping's container pointer, never `completed`: an unmapped root key,
    a trailing comma, nesting over the depth limit; an empty container is `completed` with
    nothing; an object truncated in storage and S3 stopped mid-stream end `blocked` /
    `source_unavailable`; an object damaged in place (same length, invalid bytes) rejects the
    damaged element as `invalid_utf8` and then ends `failed` / `source_changed`
J5  no marker planted in the content, a key or an invalid element is found in any report,
    locator, exception chain or log record produced by the runs above
J6  a 96 MiB document streamed from the real bucket imports with a peak of traced memory far
    below its size

Requires SOVRA_DATABASE_URL (migration owner) and SOVRA_APP_DB_PASSWORD, and an S3
endpoint as in test_ftf_0501_direct_upload_s3.py (this file boots the compose `s3`
service when nothing answers).
Run: python3 tests/integration/test_ftf_0503_json_import_postgres_s3.py
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import importlib.util
import io
import json
import logging
import sys
import tracemalloc
import urllib.parse
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "tests" / "tasks"))

import import_test_support as support  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from workers.data import import_common as ic  # noqa: E402
from workers.data import json_importer as jm  # noqa: E402

from app.auth.organization_workspace_schema import Organization, Workspace  # noqa: E402
from app.auth.rbac_middleware import Role  # noqa: E402
from app.datasets import dataset_object_source as source_module  # noqa: E402
from app.datasets import dataset_upload_store as store  # noqa: E402
from app.datasets import direct_object_storage_upload as dos  # noqa: E402
from app.datasets.direct_object_storage_upload_routes import (  # noqa: E402
    build_direct_upload_router,
)
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402
from app.services.storage_semantics_client import S3Client  # noqa: E402

CID = "FTF-0503"
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


def rechunk(chunks: Any, size: int) -> Any:
    """The same bytes cut into `size`-byte pieces."""
    pending = b""
    for chunk in chunks:
        pending += chunk
        while len(pending) >= size:
            yield pending[:size]
            pending = pending[size:]
    if pending:
        yield pending


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
    bucket = f"sovra-ftf0503-{uuid.uuid4().hex[:12]}"
    client.create_bucket(bucket)

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(p1._app_role_url(migration_url, app_password))
    check("the app engine is the restricted sovra_app role", app_engine.url.username == "sovra_app")
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=app_engine, expire_on_commit=False)

    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 0503", slug=f"acme-0503-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 0503", slug=f"globex-0503-{uuid.uuid4().hex[:8]}")
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

        proj1, _proj1b, proj2 = (
            project(org1, ws1, "p1"),
            project(org1, ws1, "p1b"),
            project(org2, ws2, "p2"),
        )
        session.add_all([proj1, _proj1b, proj2])
        session.commit()
        org1_id, org2_id = org1.id, org2.id
        p1_id, p2_id = proj1.id, proj2.id

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
    ctx1 = TenantContext(organization_id=org1_id)

    settings = dos.UploadStorageSettings(endpoint, credentials, bucket)
    fixture = p1._Fixture({"org1": org1_id, "org2": org2_id})
    app = FastAPI()
    app.include_router(
        build_direct_upload_router(
            factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
            storage_factory=lambda: settings,
            clock=lambda: datetime.now(UTC),
        )
    )
    servers = contextlib.ExitStack()
    base = servers.enter_context(p1._running_app(app))
    principals = {
        ("org1", "training"): fixture.header_for("org1", Role.ML_ENGINEER),
        ("org1", "held_out_evaluation"): fixture.header_for("org1", Role.WORKSPACE_ADMIN),
        ("org2", "training"): fixture.header_for("org2", Role.ML_ENGINEER),
    }
    org_ids = {"org1": org1_id, "org2": org2_id}

    def route(method: str, path: str, body: Any, principal: str) -> tuple[int, Any]:
        parsed = urllib.parse.urlparse(base)
        headers = {"X-Test-Principal": principal}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        http_conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=120)
        try:
            http_conn.request(method, path, body=data, headers=headers)
            response = http_conn.getresponse()
            raw = response.read()
        finally:
            http_conn.close()
        try:
            return response.status, json.loads(raw)
        except ValueError:
            return response.status, raw

    def make_dataset(
        org_label: str,
        project_id: uuid.UUID,
        connection: uuid.UUID,
        data: bytes,
        name: str,
        purpose: str = "training",
        store_bytes: bytes | None = None,
        content_type: str = "application/json",
    ) -> tuple[store.Dataset, str]:
        """Uploads `data` through the real FTF-0501 routes and returns the Dataset the
        server created. `store_bytes`, when given, overwrites the verified object in the
        bucket afterwards (storage damaged after verification)."""
        principal = principals[(org_label, purpose)]
        collection = (
            f"/api/v1/organizations/{org_ids[org_label]}/projects/{project_id}/upload-sessions"
        )
        status, created = route(
            "POST",
            collection,
            {
                "dataset_source_connection_id": str(connection),
                "purpose": purpose,
                "declared_size_bytes": len(data),
                "declared_sha256": sha(data),
                "declared_content_type": content_type,
            },
            principal,
        )
        check(f"fixture {name}: create session answers 201 (got {status} {created})", status == 201)
        session_id = created["id"]
        part_size, part_count = created["part_size_bytes"], created["part_count"]
        status, granted = route(
            "POST",
            f"{collection}/{session_id}/part-urls",
            {"part_numbers": list(range(1, part_count + 1))},
            principal,
        )
        check(f"fixture {name}: part-urls answers 200 (got {status})", status == 200)
        for grant in granted["parts"]:
            number = grant["part_number"]
            piece = data[(number - 1) * part_size : number * part_size]
            put_status = s3lab._put(grant["url"], piece)
            check(
                f"fixture {name}: part {number} PUT answers 200 (got {put_status})",
                put_status == 200,
            )
        status, done = route(
            "POST", f"{collection}/{session_id}/finalize", {"dataset_name": name}, principal
        )
        check(f"fixture {name}: finalize answers 201 (got {status} {done})", status == 201)
        with owner_engine.connect() as raw_conn:
            row = raw_conn.execute(
                text(
                    "SELECT d.id, s.object_key FROM datasets d JOIN dataset_upload_sessions s "
                    "ON s.id = d.upload_session_id WHERE s.id = :sid"
                ),
                {"sid": uuid.UUID(session_id)},
            ).one()
        dataset_id, key = row[0], str(row[1])
        if store_bytes is not None:
            client.put_object(bucket, key, store_bytes)
        with tenant_scoped_session_for(
            factory, TenantContext(organization_id=org_ids[org_label])
        ) as session:
            dataset = store.get_dataset(
                session,
                TenantContext(organization_id=org_ids[org_label]),
                project_id=project_id,
                dataset_id=dataset_id,
                include_held_out=True,
            )
        check(
            f"fixture {name}: the route-created Dataset carries the server-verified hash",
            dataset.verified_sha256 == sha(data) and dataset.verified_size_bytes == len(data),
        )
        return dataset, key

    # ---- fixtures: real JSON bytes ----------------------------------------------------
    marker = support.make_marker("json-content")
    key_marker = support.make_marker("json-key")
    bad_marker = support.make_marker("json-bad-element")
    meta_marker = support.make_marker("json-meta")
    arabic = "مرحبا بالعالم"
    pairs: list[tuple[str, str]] = [
        (arabic, f"{arabic} {marker}"),
        ("00042", "007"),
        ("=SUM(A1:A9)", "+1+1"),
        ("line one\r\nline two\nline three", 'she said "hi", then left'),
        ("@cmd|' /C calc'!A0", "-2+3"),
        ("a b c", "\U0001f468‍\U0001f469‍\U0001f467"),
        ('brackets ]}[{ and "quotes" and \\ backslash', "}]"),
    ]
    pairs += [(f"question {i}", f"answer {i}") for i in range(500)]
    expected = [text for pair in pairs for text in pair]
    good_elements = [
        json.dumps({"q": p, "a": c, key_marker: 1}, ensure_ascii=bool(i % 2)).encode("utf-8")
        for i, (p, c) in enumerate(pairs)
    ]
    bad_elements = [
        (3, b'{"q": "' + bad_marker.encode() + b'", "a": tru}', "invalid_json"),
        (7, b'["' + bad_marker.encode() + b'"]', "record_not_object"),
        (11, b'{"q": "x", "a": "y", "q": "' + bad_marker.encode() + b'"}', "duplicate_key"),
        (15, b'{"q": "x", "a": NaN}', "non_finite_number"),
        (19, b'{"q": 5, "a": "y"}', "mapping_type_mismatch"),
        (23, b'{"a": "' + bad_marker.encode() + b'"}', "required_field_missing"),
    ]
    elements: list[bytes] = []
    want_rejections: list[tuple[str, str]] = []
    for position, good in enumerate(good_elements):
        for at, raw, code in bad_elements:
            if at == position:
                elements.append(raw)
                want_rejections.append((f"record:{len(elements)}", code))
        elements.append(good)
    meta = json.dumps({"note": meta_marker, "nested": [{"k": "}]"}]}).encode()
    # a BOM, then CRLF whitespace between tokens, an ignored sibling before the container
    json_data = (
        b"\xef\xbb\xbf{\r\n  "
        + b'"_meta": '
        + meta
        + b",\r\n  "
        + b'"rows": [\r\n    '
        + b",\r\n    ".join(elements)
        + b"\r\n  ]\r\n}\r\n"
    )
    array_data = b"[" + b",\n".join(elements) + b"]"
    record_data = good_elements[1]
    mapping = ic.parse_mapping(
        json.dumps(
            {
                "shape": "prompt_completion",
                "prompt": {"key": "q"},
                "completion": {"key": "a"},
                "json": {"container": "/rows", "ignored_top_level_keys": ["_meta"]},
            }
        )
    )

    def mapping_for(container: str, ignored: tuple[str, ...] = ()) -> ic.ImportMapping:
        return ic.parse_mapping(
            json.dumps(
                {
                    "shape": "prompt_completion",
                    "prompt": {"key": "q"},
                    "completion": {"key": "a"},
                    "json": {"container": container, "ignored_top_level_keys": list(ignored)},
                }
            )
        )

    def import_dataset(
        source: source_module.DatasetObjectSource,
        *,
        chunk_size: int | None = None,
        content_type: str | None = None,
        with_mapping: ic.ImportMapping | None = None,
    ) -> tuple[jm.JsonImportOutcome, list[dict[str, object]], ic.ChunkReader]:
        chunks: Any = source.chunks
        if chunk_size is not None:
            chunks = rechunk(chunks, chunk_size)
        reader = ic.ChunkReader(
            chunks, expected_size=source.expected_size, expected_sha256=source.expected_sha256
        )
        candidates: list[dict[str, object]] = []
        outcome = jm.run_json_import(
            reader,
            with_mapping or mapping,
            verified_content_type=content_type or source.verified_content_type,
            dataset_purpose=source.purpose,
            expected_sha256=source.expected_sha256,
            on_accepted=lambda candidate: candidates.append(dict(candidate)),
        )
        return outcome, candidates, reader

    log_stream = io.StringIO()
    log_handler = logging.StreamHandler(log_stream)
    logging.getLogger().addHandler(log_handler)
    logging.getLogger().setLevel(logging.DEBUG)
    leak_values: list[Any] = []

    train, _train_key = make_dataset("org1", p1_id, conn1, json_data, "train")
    held, _held_key = make_dataset(
        "org1", p1_id, conn1, json_data, "held", purpose="held_out_evaluation"
    )
    foreign, _ = make_dataset("org2", p2_id, conn2, json_data, "foreign")

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

    def texts_of(candidates: list[dict[str, object]]) -> list[str]:
        return [
            part["text"]
            for candidate in candidates
            for message in candidate["content"]["messages"]  # type: ignore[index]
            for part in message["content"]
        ]

    # ---- J1: the real bytes, imported exactly ---------------------------------------------
    source = open_source(ctx1, p1_id, train.id)
    check(
        "J1 the dataset's verified content type is application/json",
        source.verified_content_type == "application/json",
    )
    reports = []
    for label, chunk_size in (("live chunks", None), ("1000-byte chunks", 1000)):
        outcome, candidates, reader = import_dataset(
            open_source(ctx1, p1_id, train.id), chunk_size=chunk_size
        )
        report = outcome.report
        reports.append(report)
        leak_values.append((report, outcome.fatal_locator, outcome.fatal_container))
        check(
            f"J1 {label}: completed ({report.state} {report.failure_code})",
            report.state == "completed" and report.failure_code is None,
        )
        check(
            f"J1 {label}: every good element accepted ({report.records_accepted}/{len(pairs)})",
            report.records_accepted == len(pairs),
        )
        check(
            f"J1 {label}: every bad element rejected with its own code and ordinal locator "
            f"({[(r.locator, r.code) for r in report.rejections]})",
            [(r.locator, r.code) for r in report.rejections] == want_rejections,
        )
        check(
            f"J1 {label}: records_seen is accepted + rejected",
            report.records_seen == len(pairs) + len(bad_elements) and report.blank_lines == 0,
        )
        check(
            f"J1 {label}: every text is preserved byte for byte", texts_of(candidates) == expected
        )
        check(
            f"J1 {label}: the report's source hash is the Dataset's verified hash",
            report.source_sha256 == train.verified_sha256 == sha(json_data),
        )
        check(
            f"J1 {label}: source bytes are the verified size",
            report.source_bytes == train.verified_size_bytes == len(json_data),
        )
        check(f"J1 {label}: no fatal locator", outcome.fatal_locator is None)
    check("J1 both chunkings give the identical report", reports[0] == reports[1])
    preview = ic.preview(
        jm.json_events(
            ic.ChunkReader(
                open_source(ctx1, p1_id, train.id).chunks,
                expected_size=len(json_data),
                expected_sha256=train.verified_sha256,
            ),
            mapping,
            verified_content_type="application/json",
            dataset_sha256=train.verified_sha256,
        ),
        5,
    )
    check("J1 preview over the live source returns the first 5 records", len(preview.accepted) == 5)

    array_set, _ = make_dataset("org1", p1_id, conn1, array_data, "array-root")
    outcome, candidates, _reader = import_dataset(
        open_source(ctx1, p1_id, array_set.id), with_mapping=mapping_for("$")
    )
    leak_values.append((outcome.report, outcome.fatal_locator, outcome.fatal_container))
    check(
        f"J1 a root array imports with container '$' ({outcome.report.state} "
        f"{outcome.report.failure_code})",
        outcome.report.state == "completed"
        and outcome.report.records_accepted == len(pairs)
        and [(r.locator, r.code) for r in outcome.report.rejections] == want_rejections
        and texts_of(candidates) == expected,
    )
    record_set, _ = make_dataset("org1", p1_id, conn1, record_data, "one-record")
    outcome, candidates, _reader = import_dataset(
        open_source(ctx1, p1_id, record_set.id), with_mapping=mapping_for("$record")
    )
    check(
        f"J1 a root object imports as one record with container '$record' "
        f"({outcome.report.state} {outcome.report.failure_code})",
        outcome.report.state == "completed"
        and outcome.report.records_accepted == 1
        and texts_of(candidates) == list(pairs[1]),
    )
    outcome, candidates, _reader = import_dataset(
        open_source(ctx1, p1_id, array_set.id), with_mapping=mapping_for("/rows")
    )
    check(
        f"J1 a root array under container '/rows' is container_not_found at offset:0 "
        f"({outcome.report.state} {outcome.report.failure_code} {outcome.fatal_locator})",
        (outcome.report.state, outcome.report.failure_code, outcome.fatal_locator)
        == ("failed", "container_not_found", "offset:0")
        and outcome.fatal_container == "/rows"
        and candidates == [],
    )

    # ---- J2: the content-type gate, before a byte is requested ------------------------------
    csv_data = b"q,a\n" + b"".join(f"p{i},c{i}\n".encode() for i in range(50))
    csv_set, _ = make_dataset("org1", p1_id, conn1, csv_data, "csv", content_type="text/csv")
    jsonl_data = b"\n".join(good_elements[:20]) + b"\n"
    jsonl_set, _ = make_dataset(
        "org1", p1_id, conn1, jsonl_data, "jsonl", content_type="application/jsonl"
    )
    for label, wrong_set in (("CSV", csv_set), ("JSONL", jsonl_set)):
        counting = CountingClient(client)
        wrong_source = open_source(ctx1, p1_id, wrong_set.id, storage_client=counting)
        outcome, candidates, reader = import_dataset(wrong_source)
        leak_values.append((outcome.report, outcome.fatal_locator, outcome.fatal_container))
        check(
            f"J2 a {label} dataset is refused ({outcome.report.state} "
            f"{outcome.report.failure_code})",
            outcome.report.state == "failed"
            and outcome.report.failure_code == "content_type_not_supported_by_importer"
            and candidates == [],
        )
        check(
            f"J2 {label}: not one storage call was made: {counting.calls}",
            counting.calls == [] and reader.bytes_read == 0,
        )

    # ---- J3: scoping ----------------------------------------------------------------------------
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

    failures = {
        "missing": failure(ctx1, p1_id, uuid.uuid4()),
        "foreign org": failure(ctx1, p1_id, foreign.id),
        "foreign org (their project id)": failure(ctx1, p2_id, foreign.id),
        "hidden held-out": failure(ctx1, p1_id, held.id),
    }
    check(
        f"J3 every unauthorized read is DatasetNotFoundError: {failures}",
        all(v is not None and not v.startswith("WRONG") for v in failures.values()),
    )
    check(
        f"J3 all of them read exactly alike: {set(failures.values())}",
        len(set(failures.values())) == 1,
    )
    outcome, candidates, _reader = import_dataset(
        open_source(ctx1, p1_id, held.id, include_held_out=True)
    )
    check(
        "J3 a held-out dataset imports only through include_held_out=True",
        outcome.report.state == "completed"
        and outcome.report.dataset_purpose == "held_out_evaluation",
    )

    # ---- J4: structure, damage, truncation and stopped storage ---------------------------------
    one = good_elements[1]
    structure_cases = [
        (
            "an unmapped root key",
            b'{"_meta": {}, "extra": 1, "rows": [' + one + b"]}",
            "unmapped_top_level_key",
            b'{"_meta": {}, "extra"',
            lambda data: data.index(b'"extra"'),
        ),
        (
            "a trailing comma",
            b'{"rows": [' + one + b",\n ]}",
            "json_structure_invalid",
            None,
            lambda data: data.rindex(b"]"),
        ),
        (
            "nesting over the depth limit",
            b'{"rows": [{"q": "a", "a": "b", "x": ' + b"[" * 70 + b"]" * 70 + b"}]}",
            "limit_exceeded",
            None,
            lambda data: data.index(b"[" * 70) + 61,
        ),
        (
            "a missing container",
            b'{"_meta": {"n": 1}}',
            "container_not_found",
            None,
            lambda data: data.rindex(b"}"),
        ),
        (
            "a duplicate root key",
            b'{"rows": [], "rows": []}',
            "duplicate_root_key",
            None,
            lambda data: data.index(b'"rows"', 5),
        ),
    ]
    for label, data, code, _unused, where in structure_cases:
        stored, _ = make_dataset("org1", p1_id, conn1, data, f"structure-{code}")
        outcome, candidates, _reader = import_dataset(open_source(ctx1, p1_id, stored.id))
        leak_values.append((outcome.report, outcome.fatal_locator, outcome.fatal_container))
        check(
            f"J4 {label}: failed/{code} at offset:{where(data)} naming '/rows' "
            f"({outcome.report.state} {outcome.report.failure_code} {outcome.fatal_locator} "
            f"{outcome.fatal_container})",
            (outcome.report.state, outcome.report.failure_code, outcome.fatal_locator)
            == ("failed", code, f"offset:{where(data)}")
            and outcome.fatal_container == "/rows",
        )
    empty_set, _ = make_dataset("org1", p1_id, conn1, b'{"rows": []}', "empty-container")
    outcome, candidates, _reader = import_dataset(open_source(ctx1, p1_id, empty_set.id))
    check(
        f"J4 an empty container is completed with nothing ({outcome.report.state} "
        f"{outcome.report.records_seen})",
        outcome.report.state == "completed"
        and outcome.report.records_seen == 0
        and candidates == [],
    )

    # The upload route refuses non-UTF-8 text, so the dataset is verified with good bytes and
    # the stored object is then damaged in place (same length, two invalid bytes inside a record).
    at = json_data.index(b'"question 300"') + 3
    bad = json_data[:at] + b"\xff\xfe" + json_data[at + 2 :]
    bad_set, _ = make_dataset("org1", p1_id, conn1, json_data, "bad-bytes", store_bytes=bad)
    outcome, candidates, _reader = import_dataset(open_source(ctx1, p1_id, bad_set.id))
    leak_values.append((outcome.report, outcome.fatal_locator, outcome.fatal_container))
    damaged = [r for r in outcome.report.rejections if r.code == "invalid_utf8"]
    check(
        f"J4 the damaged element is rejected as invalid_utf8 with a record locator ({damaged})",
        len(damaged) == 1 and damaged[0].locator.startswith("record:"),
    )
    check(
        f"J4 damaged-in-place storage is failed/source_changed, never completed "
        f"({outcome.report.state} {outcome.report.failure_code})",
        (outcome.report.state, outcome.report.failure_code) == ("failed", "source_changed"),
    )

    truncated, _ = make_dataset(
        "org1", p1_id, conn1, json_data, "truncated", store_bytes=json_data[:-1000]
    )
    outcome, _candidates, _reader = import_dataset(open_source(ctx1, p1_id, truncated.id))
    leak_values.append((outcome.report, outcome.fatal_locator, outcome.fatal_container))
    check(
        f"J4 an object shorter than its verified size is blocked/source_unavailable "
        f"({outcome.report.state} {outcome.report.failure_code})",
        (outcome.report.state, outcome.report.failure_code) == ("blocked", "source_unavailable"),
    )

    # ---- J6: a 96 MiB document, bounded memory --------------------------------------------------
    big_element = json.dumps({"q": "q" * 40, "a": "a" * (64 * 1024)}).encode()
    big_count = (96 * MIB) // (len(big_element) + 1)
    big = b'{"rows": [' + b",".join([big_element] * big_count) + b"]}"
    large, _ = make_dataset("org1", p1_id, conn1, big, "large")
    memory_source = open_source(ctx1, p1_id, large.id)
    memory_reader = ic.ChunkReader(
        memory_source.chunks,
        expected_size=memory_source.expected_size,
        expected_sha256=memory_source.expected_sha256,
    )
    accepted_count = [0]

    def count_accepted(_candidate: object) -> None:
        accepted_count[0] += 1

    tracemalloc.start()
    outcome = jm.run_json_import(
        memory_reader,
        mapping,
        verified_content_type=memory_source.verified_content_type,
        dataset_purpose=memory_source.purpose,
        expected_sha256=memory_source.expected_sha256,
        on_accepted=count_accepted,
    )
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    check(
        f"J6 the {len(big) // MIB} MiB document imports completely "
        f"({outcome.report.state} {accepted_count[0]}/{big_count})",
        outcome.report.state == "completed"
        and outcome.report.records_accepted == big_count == accepted_count[0],
    )
    check(
        f"J6 the traced peak ({peak // MIB} MiB) is far below the document size",
        peak < 32 * MIB and len(big) > 90 * MIB,
    )
    large_source = open_source(ctx1, p1_id, large.id)
    stopped: list[bool] = []

    def stopping_chunks() -> Any:
        for index, chunk in enumerate(large_source.chunks):
            if index == 1 and not stopped:
                stopped.append(True)
                s3lab.compose("stop", "s3", check_exit=False)
            yield chunk

    reader = ic.ChunkReader(
        stopping_chunks(),
        expected_size=large_source.expected_size,
        expected_sha256=large_source.expected_sha256,
    )
    outcome = jm.run_json_import(
        reader,
        mapping,
        verified_content_type=large_source.verified_content_type,
        dataset_purpose=large_source.purpose,
        expected_sha256=large_source.expected_sha256,
        on_accepted=lambda _candidate: None,
    )
    leak_values.append((outcome.report, outcome.fatal_locator, outcome.fatal_container))
    check(
        f"J4 stopping S3 mid-stream is blocked/source_unavailable "
        f"({outcome.report.state} {outcome.report.failure_code})",
        stopped == [True]
        and (outcome.report.state, outcome.report.failure_code)
        == ("blocked", "source_unavailable"),
    )

    # ---- J5: nothing planted leaks anywhere -----------------------------------------------------
    logging.getLogger().removeHandler(log_handler)
    leak_values.append(log_stream.getvalue())
    leaked = support.leaked_markers(leak_values, [key_marker, bad_marker, marker, meta_marker])
    check(
        f"J5 no planted key, bad-element, meta or content text reaches a report, locator or "
        f"log: {leaked}",
        not leaked,
    )
    log_text = log_stream.getvalue()
    check(
        "J5 no JSON content reaches a log record",
        marker not in log_text and arabic not in log_text and meta_marker not in log_text,
    )

    servers.close()
    app_engine.dispose()
    owner_engine.dispose()
    if booted_here:
        s3lab.compose("stop", "s3", check_exit=False)
    _finish()


def _finish() -> None:
    if FAILURES:
        print(f"FTF-0503 JSON import (PG16 + SeaweedFS): {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0503 JSON import (PG16 + SeaweedFS): all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
