#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,assignment,call-arg,index,var-annotated,misc"
"""FTF-0505 -- the XLSX importer over PostgreSQL 16 as the restricted `sovra_app` role and
the live SeaweedFS S3 API, through the real FTF-0501 upload routes and the FTF-0502-P1
dataset source reader.

`tests/tasks/test_ftf_0505.py` proves the importer on in-memory bytes. This suite proves
what only the real services can:

X1  a workbook uploaded through the real FTF-0501 routes (create -> presigned PUT ->
    finalize, declared and server-verified XLSX content type) is streamed back from the
    real bucket by `open_dataset_source` as `sovra_app`, imported by `run_xlsx_import` with
    the dataset's own verified content type, and every record is preserved exactly: Arabic
    text, leading-zero text cells, formula-looking text, CR / LF inside cells, shared and
    inline strings, for two chunkings of the live stream; formula and error cells reject
    their own rows with `row:<n>` locators; the report's source hash is
    `Dataset.verified_sha256`
X2  a dataset whose verified content type is not the XLSX type is refused with
    `content_type_not_supported_by_importer` before one byte is requested from storage
X3  a foreign organization's dataset, a missing id and a hidden held-out dataset all raise
    the same `DatasetNotFoundError`; held-out is readable only with include_held_out=True
X4  workbooks that FTF-0501 finalize accepts but that must not be imported -- a macro carrier
    inside a plain `.xlsx`-typed package (vbaProject.bin: Decision 8), an external link, a
    strict-OOXML workbook, an encrypted-entry flag and a decompression bomb past a lowered
    cap -- are stored through the real routes and refused by the importer, from the live
    bytes, with their own fatal codes and no record
X5  an object truncated in storage, and S3 stopped mid-stream, end `blocked` /
    `source_unavailable`, never `completed` or `failed`
X6  no marker planted in the cell text, a header, a sheet name or a part name is found in
    any report, locator, exception chain or log record produced by the runs above

Requires SOVRA_DATABASE_URL (migration owner) and SOVRA_APP_DB_PASSWORD, and an S3
endpoint as in test_ftf_0501_direct_upload_s3.py (this file boots the compose `s3`
service when nothing answers).
Run: python3 tests/integration/test_ftf_0505_xlsx_import_postgres_s3.py
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
import test_ftf_0505 as t505  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from workers.data import import_common as ic  # noqa: E402
from workers.data import xlsx_importer as xi  # noqa: E402

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

CID = "FTF-0505"
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
    bucket = f"sovra-ftf0505-{uuid.uuid4().hex[:12]}"
    client.create_bucket(bucket)

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(p1._app_role_url(migration_url, app_password))
    check("the app engine is the restricted sovra_app role", app_engine.url.username == "sovra_app")
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=app_engine, expire_on_commit=False)

    with Session(owner_engine) as session:
        org1 = Organization(name="Acme 0505", slug=f"acme-0505-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex 0505", slug=f"globex-0505-{uuid.uuid4().hex[:8]}")
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
        content_type: str = t505.XLSX_TYPE,
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

    # ---- fixtures: real XLSX bytes ---------------------------------------------------
    S, IS, F, E, N = t505.S, t505.IS, t505.F, t505.E, t505.N
    marker = support.make_marker("xlsx-content")
    header_marker = support.make_marker("xlsx-header")
    sheet_marker = support.make_marker("xlsx-sheet")[:30]
    arabic = "\u0645\u0631\u062d\u0628\u0627 \u0628\u0627\u0644\u0639\u0627\u0644\u0645"
    pairs: list[tuple[str, str]] = [
        (arabic, f"{arabic} {marker}"),
        ("00042", "007"),
        ("=SUM(A1:A9)", "+1+1"),
        ("line one\r\nline two\nline three", 'she said "hi", then left'),
        ("@cmd|' /C calc'!A0", "-2+3"),
    ]
    pairs += [(f"question {i}", f"answer {i}") for i in range(500)]
    expected = [text for pair in pairs for text in pair]
    rows = [[S("q"), S("a"), S(header_marker)]]
    for index, (prompt, completion) in enumerate(pairs):
        rows.append([S(prompt) if index % 2 == 0 else IS(prompt), S(completion), N("1")])
    # two rows the importer must reject, each at its own sheet row
    rows.append([S("formula row"), F("1+1", "2"), N("1")])
    rows.append([S("error row"), E("#DIV/0!"), N("1")])
    formula_row, error_row = len(pairs) + 2, len(pairs) + 3
    xlsx_data = t505.book(rows, sheet=sheet_marker)
    mapping = ic.parse_mapping(
        json.dumps(
            {
                "shape": "prompt_completion",
                "prompt": {"header": "q"},
                "completion": {"header": "a"},
                "xlsx": {"sheet_name": sheet_marker, "header_row": 0},
            }
        )
    )

    def import_dataset(
        source: source_module.DatasetObjectSource,
        *,
        chunk_size: int | None = None,
        content_type: str | None = None,
        xlsx_limits: xi.XlsxLimits = xi.DEFAULT_XLSX_LIMITS,
        use_mapping: ic.ImportMapping = mapping,
    ) -> tuple[xi.XlsxImportOutcome, list[dict[str, object]], ic.ChunkReader]:
        chunks: Any = source.chunks
        if chunk_size is not None:
            chunks = rechunk(chunks, chunk_size)
        reader = ic.ChunkReader(
            chunks, expected_size=source.expected_size, expected_sha256=source.expected_sha256
        )
        candidates: list[dict[str, object]] = []
        outcome = xi.run_xlsx_import(
            reader,
            use_mapping,
            verified_content_type=content_type or source.verified_content_type,
            dataset_purpose=source.purpose,
            expected_sha256=source.expected_sha256,
            xlsx_limits=xlsx_limits,
            on_accepted=lambda candidate: candidates.append(dict(candidate)),
        )
        return outcome, candidates, reader

    log_stream = io.StringIO()
    log_handler = logging.StreamHandler(log_stream)
    logging.getLogger().addHandler(log_handler)
    logging.getLogger().setLevel(logging.DEBUG)
    leak_values: list[Any] = []

    train, _train_key = make_dataset("org1", p1_id, conn1, xlsx_data, "train")
    held, _held_key = make_dataset(
        "org1", p1_id, conn1, xlsx_data, "held", purpose="held_out_evaluation"
    )
    foreign, _ = make_dataset("org2", p2_id, conn2, xlsx_data, "foreign")

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

    # ---- X1: the real bytes, imported exactly -------------------------------------------
    counting = CountingClient(client)
    source = open_source(ctx1, p1_id, train.id, storage_client=counting)
    check(
        "X1 the dataset's verified content type is the XLSX type",
        source.verified_content_type == t505.XLSX_TYPE,
    )
    for label, chunk_size in (("live chunks", None), ("1000-byte chunks", 1000)):
        outcome, candidates, reader = import_dataset(
            open_source(ctx1, p1_id, train.id), chunk_size=chunk_size
        )
        report = outcome.report
        leak_values.append((report, outcome.report.fatal_locator))
        check(
            f"X1 {label}: completed ({report.state} {report.failure_code})",
            report.state == "completed" and report.failure_code is None,
        )
        check(
            f"X1 {label}: every good record accepted ({report.records_accepted}/{len(pairs)})",
            report.records_accepted == len(pairs) and report.records_rejected == 2,
        )
        check(
            f"X1 {label}: the formula and error rows are rejected at their own sheet rows",
            report.rejections
            == (
                ic.RowRejection(f"row:{formula_row}", "formula_cell", "completion"),
                ic.RowRejection(f"row:{error_row}", "cell_error_value", "completion"),
            ),
        )
        got = [
            part["text"]
            for candidate in candidates
            for message in candidate["content"]["messages"]  # type: ignore[index]
            for part in message["content"]
        ]
        check(f"X1 {label}: every text is preserved byte for byte", got == expected)
        check(
            f"X1 {label}: the report's source hash is the Dataset's verified hash",
            report.source_sha256 == train.verified_sha256 == sha(xlsx_data),
        )
        check(
            f"X1 {label}: source bytes are the verified size",
            report.source_bytes == train.verified_size_bytes == len(xlsx_data),
        )
    status = ic.preview(
        xi.xlsx_events(
            ic.ChunkReader(
                open_source(ctx1, p1_id, train.id).chunks,
                expected_size=len(xlsx_data),
                expected_sha256=train.verified_sha256,
            ),
            mapping,
            verified_content_type=t505.XLSX_TYPE,
            dataset_sha256=train.verified_sha256,
        ),
        5,
    )
    check("X1 preview over the live source returns the first 5 records", len(status.accepted) == 5)

    # ---- X2: the content-type gate, before a byte is requested -----------------------------
    plain = b"".join(f"p{i},c{i}\n".encode() for i in range(50))
    plain_set, _ = make_dataset(
        "org1", p1_id, conn1, b"q,a\n" + plain, "csv", content_type="text/csv"
    )
    counting = CountingClient(client)
    plain_source = open_source(ctx1, p1_id, plain_set.id, storage_client=counting)
    outcome, candidates, reader = import_dataset(plain_source)
    leak_values.append((outcome.report, outcome.report.fatal_locator))
    check(
        f"X2 a CSV dataset is refused ({outcome.report.state} {outcome.report.failure_code})",
        outcome.report.state == "failed"
        and outcome.report.failure_code == "content_type_not_supported_by_importer"
        and candidates == [],
    )
    check(
        f"X2 not one storage call was made: {counting.calls}",
        counting.calls == [] and reader.bytes_read == 0,
    )

    # ---- X3: scoping ------------------------------------------------------------------------
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
        f"X3 every unauthorized read is DatasetNotFoundError: {failures}",
        all(v is not None and not v.startswith("WRONG") for v in failures.values()),
    )
    check(
        f"X3 all of them read exactly alike: {set(failures.values())}",
        len(set(failures.values())) == 1,
    )
    outcome, candidates, _reader = import_dataset(
        open_source(ctx1, p1_id, held.id, include_held_out=True)
    )
    check(
        "X3 a held-out dataset imports only through include_held_out=True",
        outcome.report.state == "completed"
        and outcome.report.dataset_purpose == "held_out_evaluation",
    )

    # ---- X4: accepted by the upload routes, refused by the importer ---------------------------
    small = [[IS("q"), IS("a")], [IS("p"), IS("c")]]
    ext = (
        f'<Relationship Id="rIdX" Type="{t505.REL}/externalLink" '
        f'Target="http://127.0.0.1:9/{marker}.xlsx" TargetMode="External"/>'
    )
    bomb_sheet = (
        f'{t505.XML_HEAD}<worksheet xmlns="{t505.MAIN}"><sheetData>'.encode()
        + b" " * (6 * MIB)
        + b"</sheetData></worksheet>"
    )
    attacks: list[tuple[str, bytes, str, xi.XlsxLimits]] = [
        (
            "vbaProject.bin in a plain package",
            t505.book(small, sheet=sheet_marker, extra=[("xl/vbaProject.bin", marker.encode())]),
            "unsafe_workbook",
            xi.XlsxLimits(),
        ),
        (
            "an external relationship",
            t505.book(small, sheet=sheet_marker, rel_extra=ext),
            "external_link_present",
            xi.XlsxLimits(),
        ),
        (
            "strict OOXML",
            t505.book(
                small,
                sheet=sheet_marker,
                replace={
                    "xl/workbook.xml": t505.workbook_xml(
                        [(sheet_marker, "rId1", None)], ns=t505.STRICT
                    ).encode()
                },
            ),
            "unsupported_workbook",
            xi.XlsxLimits(),
        ),
        (
            "an Excel 4 macro sheet",
            t505.book(small, sheet=sheet_marker, extra=[("xl/macrosheets/sheet1.xml", b"<m/>")]),
            "unsafe_workbook",
            xi.XlsxLimits(),
        ),
        (
            "a decompression bomb past a lowered cap",
            t505.book(small, sheet=sheet_marker, raw_sheet=bomb_sheet),
            "unsafe_archive",
            xi.XlsxLimits(max_total_uncompressed_bytes=MIB),
        ),
        (
            "an oversize shared-strings table past a lowered cap",
            t505.book(
                small,
                sheet=sheet_marker,
                shared_override=t505.shared_xml(["x" * 200_000] * 6).encode(),
            ),
            "limit_exceeded",
            xi.XlsxLimits(max_shared_strings_bytes=MIB),
        ),
    ]
    for label, data, code, limits in attacks:
        attack_set, _ = make_dataset("org1", p1_id, conn1, data, f"attack-{code}")
        outcome, candidates, _reader = import_dataset(
            open_source(ctx1, p1_id, attack_set.id), xlsx_limits=limits
        )
        leak_values.append((outcome.report, outcome.report.fatal_locator))
        check(
            f"X4 {label}: fatal {code} from the live bytes, no record "
            f"({outcome.report.state} {outcome.report.failure_code})",
            outcome.report.state == "failed"
            and outcome.report.failure_code == code
            and candidates == [],
        )
        check(
            f"X4 {label}: the source hash is still the verified hash",
            outcome.report.source_sha256 == attack_set.verified_sha256,
        )

    # ---- X5: truncated in storage, then S3 stopped mid-stream ---------------------------------
    truncated, _ = make_dataset(
        "org1", p1_id, conn1, xlsx_data, "truncated", store_bytes=xlsx_data[:-1000]
    )
    outcome, _candidates, _reader = import_dataset(open_source(ctx1, p1_id, truncated.id))
    leak_values.append((outcome.report, outcome.report.fatal_locator))
    check(
        f"X5 an object shorter than its verified size is blocked/source_unavailable "
        f"({outcome.report.state} {outcome.report.failure_code})",
        (outcome.report.state, outcome.report.failure_code) == ("blocked", "source_unavailable"),
    )

    big_header = (
        b'<row r="1"><c r="A1" t="inlineStr"><is><t>q</t></is></c>'
        b'<c r="B1" t="inlineStr"><is><t>a</t></is></c></row>'
    )
    big_sheet = (
        f'{t505.XML_HEAD}<worksheet xmlns="{t505.MAIN}"><sheetData>'.encode()
        + big_header
        + b"".join(
            f'<row r="{i}"><c r="A{i}" t="inlineStr"><is><t>{"p" * 40}</t></is></c>'
            f'<c r="B{i}" t="inlineStr"><is><t>c</t></is></c></row>'.encode()
            for i in range(2, 380_001)
        )
        + b"</sheetData></worksheet>"
    )
    stored = t505.book(small, sheet=sheet_marker, raw_sheet=big_sheet, method=0)
    large, _ = make_dataset("org1", p1_id, conn1, stored, "large")

    # Control: the same dataset read to its end with S3 left running completes. Without this a
    # stream that SeaweedFS cuts short by itself would make the stop below look effective.
    control_source = open_source(ctx1, p1_id, large.id)
    control_reader = ic.ChunkReader(
        control_source.chunks,
        expected_size=control_source.expected_size,
        expected_sha256=control_source.expected_sha256,
    )
    control = ic.preview(
        xi.xlsx_events(
            control_reader,
            mapping,
            verified_content_type=control_source.verified_content_type,
            dataset_sha256=control_source.expected_sha256,
        ),
        1,
    )
    check(
        f"X5 control: with S3 running the large dataset is read to its end "
        f"({control_reader.bytes_read}/{control_source.expected_size}) and yields a record",
        control_reader.bytes_read == control_source.expected_size and len(control.accepted) == 1,
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
    outcome = xi.run_xlsx_import(
        reader,
        mapping,
        verified_content_type=large_source.verified_content_type,
        dataset_purpose=large_source.purpose,
        expected_sha256=large_source.expected_sha256,
        on_accepted=lambda _candidate: None,
    )
    leak_values.append((outcome.report, outcome.report.fatal_locator))
    check(
        f"X5 stopping S3 mid-stream is blocked/source_unavailable "
        f"({outcome.report.state} {outcome.report.failure_code})",
        stopped == [True]
        and (outcome.report.state, outcome.report.failure_code)
        == ("blocked", "source_unavailable"),
    )
    check(
        f"X5 the stop cut the read short ({reader.bytes_read}/{large_source.expected_size} bytes) "
        "where the control read everything",
        reader.bytes_read < large_source.expected_size,
    )

    # ---- X6: nothing planted leaks anywhere -------------------------------------------------
    logging.getLogger().removeHandler(log_handler)
    leak_values.append(log_stream.getvalue())
    leaked = support.leaked_markers(leak_values, [header_marker, marker, sheet_marker])
    check(
        f"X6 no planted header, sheet name or content text reaches a report, locator or log "
        f"record: {leaked}",
        not leaked,
    )
    log_text = log_stream.getvalue()
    check(
        "X6 no workbook content reaches a log record",
        marker not in log_text and arabic not in log_text,
    )

    servers.close()
    app_engine.dispose()
    owner_engine.dispose()
    if booted_here:
        s3lab.compose("stop", "s3", check_exit=False)
    _finish()


def _finish() -> None:
    if FAILURES:
        print(f"FTF-0505 XLSX import (PG16 + SeaweedFS): {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0505 XLSX import (PG16 + SeaweedFS): all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
