#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,assignment,call-arg"
# mypy: disable-error-code="call-overload,index,misc,union-attr"
"""FTF-H011-P1 -- the dataset mapping probe/preview service
(`apps/api/app/datasets/dataset_mapping_service.py`) over PostgreSQL 16 as the restricted
`sovra_app` role and the live SeaweedFS S3 API, through the real FTF-0501 upload routes and the
FTF-0502-P1 dataset source reader.

`tests/tasks/test_ftf_h011_p1.py` proves `workers/data/dataset_mapping_review.py` (pure, no DB/S3)
in full. This suite proves what only the real services can:

I1  a real JSONL dataset uploaded through the live FTF-0501 routes previews with its own real raw
    rows (masked / revealed correctly) and real accepted/rejected candidates
I2  a foreign organization's dataset, a foreign project's dataset, a missing id and a hidden
    held-out dataset are all the same `DatasetNotFoundError` -- identical to FTF-0502-P1's own I2
I3  a held-out dataset previews with `include_held_out=True` and is refused without it
I4  a revoked connection denies a new preview exactly as it denies a new import (Section 67.4) --
    and makes no storage call at all: authorization runs before the bytes are ever opened
I5  a real CSV dataset's headers and rows round-trip through the probe and the preview alike
I6  a storage outage (the compose `s3` service stopped before the first real read) is
    `blocked`/`source_unavailable`, never a raised exception, over the real network

Requires SOVRA_DATABASE_URL (migration owner) and SOVRA_APP_DB_PASSWORD, and an S3 endpoint as in
test_ftf_0501_direct_upload_s3.py (this file boots the compose `s3` service when nothing answers).
Run: python3 tests/integration/test_ftf_h011_p1_mapping_preview_postgres_s3.py
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import importlib.util
import json
import socket
import sys
import threading
import urllib.parse
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from fastapi import FastAPI  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from workers.data import import_common as ic  # noqa: E402

from app.auth.organization_workspace_schema import Organization, Workspace  # noqa: E402
from app.auth.rbac_middleware import Role  # noqa: E402
from app.datasets import dataset_mapping_service as mapping_service  # noqa: E402
from app.datasets import dataset_object_source as source_module  # noqa: E402
from app.datasets import dataset_source_connection_crud as conn_crud  # noqa: E402
from app.datasets import dataset_upload_store as store  # noqa: E402
from app.datasets import direct_object_storage_upload as dos  # noqa: E402
from app.datasets.direct_object_storage_upload_routes import (  # noqa: E402
    build_direct_upload_router,
)
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402
from app.services.storage_semantics_client import S3Client  # noqa: E402

CID = "FTF-H011-P1"
FAILURES: list[str] = []
HERE = Path(__file__).resolve().parent
MIB = 1024**2


def _load(name: str, directory: Path = HERE) -> Any:
    spec = importlib.util.spec_from_file_location(name[:-3], directory / name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


p1 = _load("test_ftf_0501_p1_dataset_uploads.py")
s3lab = _load("test_ftf_0501_direct_upload_s3.py")
# The pure suite's own META-LEAK helper (root logging at DEBUG, warnings, stdout, stderr), reused
# here so the same channels are captured around the real service, not a second copy of it.
unit = _load("test_ftf_h011_p1.py", HERE.parent / "tasks")


def check(description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{CID}] {description}")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CountingClient:
    """Delegates to a real S3Client and records every call by name (FTF-0502-P1's own
    precedent): proves I4's own "no storage call at all" claim directly, never by absence of
    a side effect this test cannot observe."""

    def __init__(self, inner: S3Client) -> None:
        self._inner = inner
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def stream_object_windows(
        self, bucket: str, key: str, *, window_bytes: int = 8 * MIB, chunk_size: int = MIB
    ) -> Any:
        self.calls.append(("stream_object_windows", (bucket, key)))
        return self._inner.stream_object_windows(
            bucket, key, window_bytes=window_bytes, chunk_size=chunk_size
        )

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._inner, name)

        def recorded(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args))
            return target(*args, **kwargs)

        return recorded


PROMPT_COMPLETION = ic.mapping_from_object(
    {"shape": "prompt_completion", "prompt": {"key": "prompt"}, "completion": {"key": "completion"}}
)
CSV_PROMPT_COMPLETION = ic.mapping_from_object(
    {
        "shape": "prompt_completion",
        "csv": {"encoding": "utf-8", "delimiter": ",", "has_header": True, "header_row": 0},
        "prompt": {"header": "q"},
        "completion": {"header": "a"},
    }
)


def _allowed_endpoints(*pairs: tuple[str, int]) -> set[tuple[str, int]]:
    """Every (host, port) the preview path may talk to, plus the addresses those names resolve to
    (a connect event carries the resolved address, never the configured name)."""
    allowed: set[tuple[str, int]] = set()
    for host, port in pairs:
        allowed.add((host, port))
        for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
            allowed.add((info[4][0], info[4][1]))
    return allowed


def _audited(fn: Any, allowed: set[tuple[str, int]]) -> tuple[list[Any], list[str]]:
    """Runs `fn` once unhooked (warms every lazy import and cache: the first call is not the
    steady-state preview path), then again under an audit hook confined to this thread. Returns
    (network events outside `allowed`, compile/exec events)."""
    fn()
    me = threading.get_ident()
    hosts = {host for host, _port in allowed}
    active = [True]
    outside: list[Any] = []
    code: list[str] = []

    def hook(event: str, args: tuple[Any, ...]) -> None:
        if not active[0] or threading.get_ident() != me:
            return
        if event in ("socket.connect", "socket.sendto"):
            address = args[1]
            endpoint = tuple(address[:2]) if isinstance(address, tuple) else address
            if endpoint not in allowed:
                outside.append((event, endpoint))
        elif event in ("socket.getaddrinfo", "socket.gethostbyname"):
            if args[0] not in hosts:
                outside.append((event, args[0]))
        elif event in ("compile", "exec"):
            code.append(event)

    sys.addaudithook(hook)
    try:
        fn()
    finally:
        active[0] = False
    return outside, code


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
    bucket = f"sovra-ftfh011p1-{uuid.uuid4().hex[:12]}"
    client.create_bucket(bucket)

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(p1._app_role_url(migration_url, app_password))
    check("the app engine is the restricted sovra_app role", app_engine.url.username == "sovra_app")
    factory = sessionmaker(bind=app_engine, expire_on_commit=False)

    with Session(owner_engine) as session:
        org1 = Organization(name="Acme H011P1", slug=f"acme-h011p1-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex H011P1", slug=f"globex-h011p1-{uuid.uuid4().hex[:8]}")
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

    app = FastAPI()
    settings = dos.UploadStorageSettings(endpoint, credentials, bucket)
    fixture = p1._Fixture({"org1": org1_id, "org2": org2_id})
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
        *,
        content_type: str = "application/jsonl",
        purpose: str = "training",
    ) -> store.Dataset:
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
        with tenant_scoped_session_for(
            factory, TenantContext(organization_id=org_ids[org_label])
        ) as session:
            dataset = store.get_dataset(
                session,
                TenantContext(organization_id=org_ids[org_label]),
                project_id=project_id,
                dataset_id=uuid.UUID(done["id"]),
                include_held_out=True,
            )
        check(
            f"fixture {name}: the route-created Dataset carries the server-verified hash",
            dataset.verified_sha256 == sha(data) and dataset.verified_size_bytes == len(data),
        )
        return dataset

    def preview(
        ctx: TenantContext,
        project_id: uuid.UUID,
        dataset_id: uuid.UUID,
        mapping: ic.ImportMapping,
        *,
        include_held_out: bool = False,
        reveal: bool,
        storage_client: Any = None,
    ) -> mapping_service.MappingPreviewResult:
        with tenant_scoped_session_for(factory, ctx) as session:
            return mapping_service.preview_dataset_mapping(
                session,
                ctx,
                project_id=project_id,
                dataset_id=dataset_id,
                include_held_out=include_held_out,
                storage=source_module.DatasetObjectStorage(storage_client or client, bucket),
                mapping=mapping,
                reveal=reveal,
            )

    def probe(
        ctx: TenantContext,
        project_id: uuid.UUID,
        dataset_id: uuid.UUID,
        probe_mapping: ic.ImportMapping,
        *,
        include_held_out: bool = False,
        reveal: bool,
        storage_client: Any = None,
    ) -> mapping_service.ProbeResult:
        with tenant_scoped_session_for(factory, ctx) as session:
            return mapping_service.probe_dataset_source(
                session,
                ctx,
                project_id=project_id,
                dataset_id=dataset_id,
                include_held_out=include_held_out,
                storage=source_module.DatasetObjectStorage(storage_client or client, bucket),
                probe_mapping=probe_mapping,
                reveal=reveal,
            )

    def line(i: int) -> bytes:
        return (
            json.dumps(
                {"prompt": f"question {i}", "completion": f"answer {i}", "ssn": "1-2-3"}
            ).encode()
            + b"\n"
        )

    # ---- I1: a real JSONL dataset previews with its own real raw rows ------------------
    small = b"".join(line(i) for i in range(10))
    train = make_dataset("org1", p1_id, conn1, small, "train")
    masked = preview(ctx1, p1_id, train.id, PROMPT_COMPLETION, reveal=False)
    check(
        "I1 the real dataset previews completed, with real accepted rows",
        masked.state == "completed" and masked.counts["accepted"] == 10,
    )

    def _is_masked_marker(value: Any) -> bool:
        return (
            isinstance(value, dict)
            and value.get("masked") is True
            and isinstance(value["chars"], int)
        )

    check(
        "I1 masking applies to the real stored bytes too (every key and value, MASK-LEAK)",
        all(
            _is_masked_marker(entry["key"]) and _is_masked_marker(entry["value"])
            for row in masked.rows
            for entry in row.raw_row
        )
        and "question 0" not in str(masked.rows[0].raw_row)
        and "prompt" not in str(masked.rows[0].raw_row),
    )
    candidate_type_tags_ok = True
    candidate_text_masked = True
    for row in masked.rows:
        if row.candidate is None:
            continue
        for message in row.candidate["content"]["messages"]:
            for part in message["content"]:
                if part["type"] not in ("text", "json"):
                    candidate_type_tags_ok = False
                if part["type"] == "text" and not _is_masked_marker(part["text"]):
                    candidate_text_masked = False
    check(
        "I1 the candidate's own JSON type tags stay in clear (disclosed MASK-LEAK exception)",
        candidate_type_tags_ok,
    )
    check("I1 the candidate's own text content is masked", candidate_text_masked)
    check(
        "I1 not_mapped column names are masked too",
        all(_is_masked_marker(name) for row in masked.rows for name in row.not_mapped),
    )
    check(
        "I1 none of the real text leaks through the masked candidates",
        "question 0" not in str([row.candidate for row in masked.rows]),
    )
    revealed = preview(ctx1, p1_id, train.id, PROMPT_COMPLETION, reveal=True)
    check(
        "I1 reveal=True shows the real stored text",
        revealed.rows[0].raw_row
        == {"prompt": "question 0", "completion": "answer 0", "ssn": "1-2-3"},
    )
    check("I1 the not_mapped column is named", revealed.rows[0].not_mapped == ("ssn",))

    probe_mapping = ic.mapping_from_object(
        {"shape": "prompt_completion", "prompt": {"key": "prompt"}, "completion": {"key": "prompt"}}
    )
    probed = probe(ctx1, p1_id, train.id, probe_mapping, reveal=True)
    check(
        "I1 a probe over the same real dataset returns real raw rows",
        len(probed.raw_rows) == 10 and probed.raw_rows[0]["prompt"] == "question 0",
    )

    # ---- I2: scoping, identical to FTF-0502-P1's own I2 --------------------------------
    held = make_dataset(
        "org1", p1_id, conn1, small + line(99), "held", purpose="held_out_evaluation"
    )
    foreign = make_dataset("org2", p2_id, conn2, small + line(7), "foreign")

    def failure(
        ctx: TenantContext, project_id: uuid.UUID, dataset_id: uuid.UUID, **kw: Any
    ) -> str | None:
        try:
            preview(ctx, project_id, dataset_id, PROMPT_COMPLETION, reveal=True, **kw)
        except store.DatasetNotFoundError as exc:
            return str(exc).replace(str(dataset_id), "<id>")
        except BaseException as exc:  # noqa: BLE001 - the test inspects the type
            return f"WRONG {type(exc).__name__}"
        return None

    missing_id = uuid.uuid4()
    failures = {
        "missing": failure(ctx1, p1_id, missing_id),
        "foreign org": failure(ctx1, p1_id, foreign.id),
        "foreign project": failure(ctx1, p1b_id, train.id),
        "wrong tenant": failure(ctx2, p1_id, train.id),
        "hidden held-out": failure(ctx1, p1_id, held.id),
    }
    check(
        f"I2 every unauthorized preview is DatasetNotFoundError: {failures}",
        all(v is not None and not v.startswith("WRONG") for v in failures.values()),
    )
    check(
        f"I2 all of them read exactly alike: {set(failures.values())}",
        len(set(failures.values())) == 1,
    )

    # ---- I3: held-out -------------------------------------------------------------------
    held_preview = preview(
        ctx1, p1_id, held.id, PROMPT_COMPLETION, include_held_out=True, reveal=True
    )
    check(
        "I3 a held-out dataset previews with include_held_out=True",
        held_preview.state == "completed" and held_preview.counts["accepted"] == 11,
    )
    check("I3 it is refused without it", failures["hidden held-out"] is not None)

    # ---- I4: a revoked connection denies a new preview, before any storage call --------
    revoke_conn = seed_connection(org1_id, p1_id)
    revoked_dataset = make_dataset("org1", p1_id, revoke_conn, small, "revoke-me")
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE dataset_source_connections SET connection_status = 'revoked' WHERE id = :id"
            ),
            {"id": revoke_conn},
        )
    counting = CountingClient(client)
    denied = None
    try:
        preview(
            ctx1, p1_id, revoked_dataset.id, PROMPT_COMPLETION, reveal=True, storage_client=counting
        )
    except conn_crud.DatasetSourceAuthorizationError as exc:
        denied = exc
    check("I4 a revoked connection denies the preview", denied is not None)
    check(f"I4 no storage call was made before the denial: {counting.calls}", counting.calls == [])

    # ---- I5: CSV round trip --------------------------------------------------------------
    csv_data = "q,a,secret\nhello,world,123-45-6789\nمرحبا,عالم,456\n".encode()
    csv_dataset = make_dataset("org1", p1_id, conn1, csv_data, "csv-train", content_type="text/csv")
    csv_preview = preview(ctx1, p1_id, csv_dataset.id, CSV_PROMPT_COMPLETION, reveal=True)
    check(
        "I5 a real CSV dataset's headers and rows round-trip",
        csv_preview.source_schema_sha256 is not None
        and csv_preview.rows[0].raw_row == {"q": "hello", "a": "world", "secret": "123-45-6789"}
        and csv_preview.rows[1].raw_row["q"] == "مرحبا",
    )
    csv_probe_mapping = ic.mapping_from_object(
        {
            "shape": "prompt_completion",
            "csv": {"encoding": "utf-8", "delimiter": ",", "has_header": True, "header_row": 0},
            "prompt": {"index": 0},
            "completion": {"index": 0},
        }
    )
    csv_probed = probe(ctx1, p1_id, csv_dataset.id, csv_probe_mapping, reveal=True)
    check(
        "I5 the probe discovers the real CSV headers",
        csv_probed.headers == ("q", "a", "secret"),
    )

    # ---- I8: nested-key masking on the real stored bytes (whole-result canary scans) -----
    from dataclasses import asdict  # noqa: PLC0415

    from app.tools.tool_definition_crud import ActorInfo, create_tool_definition  # noqa: PLC0415

    def add_tool(ctx: TenantContext, project_id: uuid.UUID, name: str) -> None:
        with tenant_scoped_session_for(factory, ctx) as session:
            create_tool_definition(
                session,
                ctx,
                project_id=project_id,
                name=name,
                description=None,
                input_schema_json={
                    "type": "object",
                    "properties": {"order_id": {"type": "string"}, "type": {"type": "string"}},
                    "required": ["order_id"],
                    "additionalProperties": False,
                },
                output_schema_json={"type": "object"},
                side_effect_class="read_only",
                requires_confirmation=False,
                is_critical=False,
                actor=ActorInfo("user", "seed", uuid.uuid4().hex),
            )

    add_tool(ctx1, p1_id, "lookup_order")
    add_tool(ctx1, p1b_id, "get_other_project_order")
    tool_mapping = ic.mapping_from_object(
        {
            "shape": "messages_json",
            "messages": {"key": "messages"},
            "role_key": "role",
            "content_key": "content",
            "tool_calls_key": "tool_calls",
            "tool_call_id_key": "tool_call_id",
            "name_key": "name",
            "role_map": {"user": "user", "assistant": "assistant", "tool": "tool"},
            "on_unmapped_message_keys": "ignore",
        }
    )

    def tool_row(
        call_name: str = "lookup_order",
        reply_id: str = "call_1",
        arguments: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> bytes:
        user: dict[str, Any] = {"role": "user", "content": "where is it"}
        user.update(extra or {})
        return (
            json.dumps(
                {
                    "messages": [
                        user,
                        {
                            "role": "assistant",
                            "content": "checking",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "name": call_name,
                                    "arguments": arguments or {"order_id": "abc"},
                                }
                            ],
                        },
                        {
                            "role": "tool",
                            "content": "found",
                            "tool_call_id": reply_id,
                            "name": call_name,
                        },
                    ]
                }
            ).encode()
            + b"\n"
        )

    canary_args = {"order_id": "abc", "type": "CANARY-TYPE-VALUE"}
    canary_extra = {"CANARY-NESTED-KEY": {"CANARY-JSON-KEY": "CANARY-NESTED-VALUE"}}
    tool_data = (
        tool_row(arguments=canary_args, extra=canary_extra)  # 1 valid
        + tool_row(call_name="not_defined_anywhere")  # 2 unknown tool
        + tool_row(reply_id="call_orphan")  # 3 orphan id
        + tool_row(call_name="get_other_project_order")  # 4 foreign project's tool
        + tool_row(arguments={"order_id": 123})  # 5 bad-typed argument
    )
    tool_dataset = make_dataset("org1", p1_id, conn1, tool_data, "tool-calls")
    tool_masked = preview(ctx1, p1_id, tool_dataset.id, tool_mapping, reveal=False)
    outcomes = [r.outcome for r in tool_masked.rows]
    check(
        f"I9 real tool definitions: valid call accepted, unknown/orphan/foreign/bad-typed "
        f"rejected ({outcomes})",
        outcomes == ["accepted", "rejected", "rejected", "rejected", "rejected"],
    )
    check(
        "I9 the rejections carry a machine-readable code",
        all(r.code is not None for r in tool_masked.rows[1:]),
    )
    canaries = ("CANARY-TYPE-VALUE", "CANARY-NESTED-KEY", "CANARY-JSON-KEY", "CANARY-NESTED-VALUE")
    scan = repr(tool_masked) + json.dumps(asdict(tool_masked), default=repr)
    check(
        f"I8 no nested raw key or tool argument survives the masked preview "
        f"(repr and asdict): {[c for c in canaries if c in scan]}",
        not any(c in scan for c in canaries),
    )
    tool_probe = probe(ctx1, p1_id, tool_dataset.id, tool_mapping, reveal=False)
    probe_scan = repr(tool_probe) + json.dumps(asdict(tool_probe), default=repr)
    check(
        f"I8 no nested raw key survives the masked probe (repr and asdict): "
        f"{[c for c in canaries if c in probe_scan]}",
        not any(c in probe_scan for c in canaries),
    )
    tool_revealed = preview(ctx1, p1_id, tool_dataset.id, tool_mapping, reveal=True)
    reveal_scan = repr(tool_revealed)
    check(
        "I8 positive control: reveal=True shows the canaries",
        "CANARY-TYPE-VALUE" in reveal_scan and "CANARY-NESTED-KEY" in reveal_scan,
    )

    # ---- I10: ';' CSV with an Arabic header on the third record (UTF-8) -------------------
    # FTF-0501 finalize refuses a text/csv object that is not UTF-8, so a windows-1256 CSV can
    # never be stored through the real routes; that decoding is pinned on the real importer in
    # tests/tasks/test_ftf_h011_p1.py (CP1256-*). Here: the same shape in UTF-8 on real storage,
    # and the refusal of the cp1256 object itself.
    arabic_text = "ملاحظة;سطر\nأخرى;سطر\nسؤال;جواب;سري\nمرحبا;عالم;123\n"
    arabic_mapping = ic.mapping_from_object(
        {
            "shape": "prompt_completion",
            "csv": {"encoding": "utf-8", "delimiter": ";", "has_header": True, "header_row": 2},
            "prompt": {"header": "سؤال"},
            "completion": {"header": "جواب"},
        }
    )
    arabic_dataset = make_dataset(
        "org1", p1_id, conn1, arabic_text.encode(), "arabic-semicolon", content_type="text/csv"
    )
    arabic_preview = preview(ctx1, p1_id, arabic_dataset.id, arabic_mapping, reveal=True)
    check(
        f"I10 the ';' CSV with its Arabic header on record 3 previews "
        f"({arabic_preview.state}/{arabic_preview.failure_code})",
        arabic_preview.state == "completed"
        and arabic_preview.rows[0].outcome == "accepted"
        and arabic_preview.rows[0].raw_row == {"سؤال": "مرحبا", "جواب": "عالم", "سري": "123"},
    )
    cp1256_bytes = arabic_text.encode("cp1256")
    cp_collection = f"/api/v1/organizations/{org1_id}/projects/{p1_id}/upload-sessions"
    cp_principal = principals[("org1", "training")]
    cp_status, cp_created = route(
        "POST",
        cp_collection,
        {
            "dataset_source_connection_id": str(conn1),
            "purpose": "training",
            "declared_size_bytes": len(cp1256_bytes),
            "declared_sha256": sha(cp1256_bytes),
            "declared_content_type": "text/csv",
        },
        cp_principal,
    )
    check(f"I10 cp1256 fixture: create session answers 201 (got {cp_status})", cp_status == 201)
    cp_status, cp_granted = route(
        "POST",
        f"{cp_collection}/{cp_created['id']}/part-urls",
        {"part_numbers": list(range(1, cp_created["part_count"] + 1))},
        cp_principal,
    )
    for grant in cp_granted["parts"]:
        size = cp_created["part_size_bytes"]
        s3lab._put(
            grant["url"],
            cp1256_bytes[(grant["part_number"] - 1) * size : grant["part_number"] * size],
        )
    cp_status, cp_done = route(
        "POST",
        f"{cp_collection}/{cp_created['id']}/finalize",
        {"dataset_name": "cp1256-refused"},
        cp_principal,
    )
    check(
        f"I10 a windows-1256 text/csv object is refused at finalize, so it never reaches a "
        f"preview (got {cp_status} {cp_done})",
        cp_status != 201 and "id" not in (cp_done if isinstance(cp_done, dict) else {}),
    )

    # ---- I11: JSON container pointer -----------------------------------------------------
    json_doc = json.dumps(
        {"meta": {"v": 1}, "rows": [{"prompt": "p1", "completion": "c1"}, {"prompt": "p2"}]}
    ).encode()
    json_mapping = ic.mapping_from_object(
        {
            "shape": "prompt_completion",
            "json": {"container": "/rows", "ignored_top_level_keys": ["meta"]},
            "prompt": {"key": "prompt"},
            "completion": {"key": "completion"},
        }
    )
    json_dataset = make_dataset(
        "org1", p1_id, conn1, json_doc, "json-container", content_type="application/json"
    )
    json_preview = preview(ctx1, p1_id, json_dataset.id, json_mapping, reveal=True)
    check(
        f"I11 a JSON container pointer previews one accepted and one rejected row "
        f"({json_preview.state}/{[r.outcome for r in json_preview.rows]})",
        json_preview.state == "completed"
        and [r.outcome for r in json_preview.rows] == ["accepted", "rejected"],
    )

    # ---- I12: a SAME-SIZE real overwrite, past the previewed rows, is caught ---------------
    same_data = b"".join(line(i) for i in range(50))
    same_dataset = make_dataset("org1", p1_id, conn1, same_data, "same-size-overwrite")
    with tenant_scoped_session_for(factory, ctx1) as session:
        same_key = store.get_upload_session(
            session, ctx1, p1_id, same_dataset.upload_session_id
        ).object_key
    same_tampered = same_data.replace(b'"answer 35"', b'"answer 53"', 1)
    check("I12 fixture: the tampered body is the same size", len(same_tampered) == len(same_data))
    check("I12 fixture: the tampered body differs", same_tampered != same_data)
    client.put_object(bucket, same_key, same_tampered)
    same_preview = preview(ctx1, p1_id, same_dataset.id, PROMPT_COMPLETION, reveal=True)
    check(
        f"I12 a same-size overwrite is failed/source_changed ({same_preview.state}/"
        f"{same_preview.failure_code})",
        same_preview.state == "failed" and same_preview.failure_code == "source_changed",
    )
    same_probe = probe(ctx1, p1_id, same_dataset.id, probe_mapping, reveal=True)
    check(
        f"I12b the probe catches it too ({same_probe.state}/{same_probe.failure_code})",
        same_probe.state == "failed" and same_probe.failure_code == "source_changed",
    )

    # ---- I7: SOURCE-CHANGED -- a real object overwritten after verification, past what a
    # bounded (20-row) preview would ever need to read on its own, is still caught -------------
    overwrite_rows = 50
    overwrite_data = b"".join(line(i) for i in range(overwrite_rows))
    overwrite_dataset = make_dataset("org1", p1_id, conn1, overwrite_data, "overwrite-me")
    with tenant_scoped_session_for(factory, ctx1) as session:
        overwrite_upload = store.get_upload_session(
            session, ctx1, p1_id, overwrite_dataset.upload_session_id
        )
        overwrite_key = overwrite_upload.object_key
    tampered = b"".join(line(i) for i in range(overwrite_rows)) + line(overwrite_rows) * 5
    client.put_object(bucket, overwrite_key, tampered)
    overwritten_preview = preview(ctx1, p1_id, overwrite_dataset.id, PROMPT_COMPLETION, reveal=True)
    check(
        f"I7 the real overwritten object is detected (failed/source_changed), never reported "
        f"as a clean completed preview, even though only the first 20 of {overwrite_rows} rows "
        f"were ever needed to fill the preview limit (got {overwritten_preview.state}/"
        f"{overwritten_preview.failure_code})",
        overwritten_preview.state == "failed"
        and overwritten_preview.failure_code == "source_changed",
    )
    overwritten_probe = probe(ctx1, p1_id, overwrite_dataset.id, probe_mapping, reveal=True)
    check(
        f"I7b the probe also detects the real overwrite (got {overwritten_probe.state}/"
        f"{overwritten_probe.failure_code})",
        overwritten_probe.state == "failed" and overwritten_probe.failure_code == "source_changed",
    )

    # ---- I13: the REAL service, not the pure path, opens no foreign socket, runs no
    # eval/exec and leaks no raw row into any side channel (F14 / R16 / R18) ---------------------
    allowed = _allowed_endpoints(
        (endpoint.host, endpoint.port),
        (str(app_engine.url.host), int(app_engine.url.port or 5432)),
    )
    counting = CountingClient(client)
    audited_results: list[Any] = []

    def real_service() -> None:
        audited_results.clear()
        audited_results.append(
            preview(ctx1, p1_id, train.id, PROMPT_COMPLETION, reveal=True, storage_client=counting)
        )
        audited_results.append(
            probe(ctx1, p1_id, train.id, probe_mapping, reveal=True, storage_client=counting)
        )

    outside, code = _audited(real_service, allowed)
    check(
        "I13 fixture: the audited real calls completed against real storage "
        f"({[getattr(r, 'state', None) for r in audited_results]})",
        len(audited_results) == 2
        and all(r.state == "completed" for r in audited_results)
        and any(name == "stream_object_windows" for name, _args in counting.calls),
    )
    check(
        f"I13 F14 the real service opens a socket only to the storage endpoint and the DB: "
        f"{outside}",
        outside == [],
    )
    check(
        f"I13 R16 the real service executes no compile/exec/eval after warm-up: {code}",
        code == [],
    )

    def positive_control() -> None:
        sock = socket.socket()
        sock.settimeout(0.05)
        with contextlib.suppress(OSError):
            sock.connect(("203.0.113.9", 80))
        sock.close()
        eval("1 + 1")  # noqa: S307 - positive control for the audit hook

    control_outside, control_code = _audited(positive_control, allowed)
    check(
        "I13 positive control: the same hook sees a connect to 203.0.113.9:80 and an eval "
        f"({control_outside}, {control_code})",
        ("socket.connect", ("203.0.113.9", 80)) in control_outside and "compile" in control_code,
    )

    needles = ("question 0", "answer 0", "1-2-3")
    leaks = {n: unit._meta_leaks(real_service, n) for n in needles}
    check(
        f"I13 R18 no raw row text reaches logging/warnings/stdout/stderr from the real "
        f"service: {leaks}",
        all(found == [] for found in leaks.values()),
    )

    def logged_control() -> None:
        import logging

        logging.getLogger("h011").warning("question 0 logged by the service")
        print("answer 0 printed")

    check(
        "I13 positive control: the shared META-LEAK helper sees a logged and a printed raw value",
        set(unit._meta_leaks(logged_control, "question 0")) == {"logging"}
        and set(unit._meta_leaks(logged_control, "answer 0")) == {"stdout"},
    )

    # ---- I6: storage outage before the first real read -------------------------------------
    big = (line(1) * (MIB // len(line(1)) + 1))[:MIB] * 24
    big_dataset = make_dataset("org1", p1_id, conn1, big, "big")
    outage_result = None
    with tenant_scoped_session_for(factory, ctx1) as session:
        outage_result_holder: list[mapping_service.MappingPreviewResult] = []

        def run_preview() -> None:
            outage_result_holder.append(
                mapping_service.preview_dataset_mapping(
                    session,
                    ctx1,
                    project_id=p1_id,
                    dataset_id=big_dataset.id,
                    include_held_out=False,
                    storage=source_module.DatasetObjectStorage(client, bucket),
                    mapping=PROMPT_COMPLETION,
                    reveal=True,
                )
            )

        s3lab.compose("stop", "s3", check_exit=False)
        run_preview()
        outage_result = outage_result_holder[0] if outage_result_holder else None
    check(
        f"I6 a storage outage is blocked/source_unavailable, never raised ({outage_result})",
        outage_result is not None
        and outage_result.state == "blocked"
        and outage_result.failure_code == "source_unavailable",
    )
    if booted_here:
        s3lab.compose("up", "-d", "s3")
        s3lab._wait_for_tcp(endpoint.host, endpoint.port, timeout_seconds=90.0)
        s3lab._wait_for_s3_http_ready(endpoint, timeout_seconds=90.0)

    servers.close()
    app_engine.dispose()
    owner_engine.dispose()
    if booted_here:
        s3lab.compose("stop", "s3", check_exit=False)
    _finish()


def _finish() -> None:
    if FAILURES:
        print(f"FTF-H011-P1 dataset mapping preview (PG16 + SeaweedFS): {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-H011-P1 dataset mapping preview (PG16 + SeaweedFS): all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
