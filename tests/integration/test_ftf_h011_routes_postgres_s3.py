#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,assignment,call-arg,call-overload,index,misc,union-attr,no-any-return,var-annotated"
# ruff: noqa: E501
"""FTF-H011-P3 -- the dataset mapping routes (`apps/api/app/datasets/dataset_mapping_routes.py`) on
a real uvicorn server, over PostgreSQL 16 as the restricted `sovra_app` role (the full migration
chain, 0036 included) and the live S3 API, with datasets uploaded through the real FTF-0501 routes.

`tests/tasks/test_ftf_h011.py` proves the routes, the contract and the wiring without a database. This
suite proves what only the real services can:

I0   migration 0036 upgrade / downgrade / upgrade with one head; ENABLE + FORCE row-level security
     and a non-allow-all policy; SELECT/INSERT only for `sovra_app`; raw `sovra_app` SQL refused for an
     overwrite, a delete, a cross-tenant row, a malformed digest / action / key, a rationale on a
     proposal, no rationale on a decision and a repeated key
I1   a real Arabic JSONL dataset: probe, preview, propose, get, approve; an independent DB check of
     the approval (approver, purpose, the exact `mapping_sha256`, the stored key, digest and
     rationale) and of the content-free audit trail; replay and key reuse
I2   real tool definitions: a valid tool call is accepted; an unknown tool and an orphan tool id are
     rejected rows shown by the preview
I3   no assistant in `role_map` is a 400 and stores nothing
I4   an unmapped raw role in the data is proposable but never approvable (409), and rejectable
I5   deny training for evaluation-only grants (403 / 409), a grant narrowed after the proposal (403)
I6   masking by role and connection classification, with canary scans of every response
I7   foreign dataset / foreign project / foreign organization / hidden held-out dataset
I8   concurrency: 12 identical proposals, 8 identical approvals, approve-vs-reject
I9   a windows-1256 `;` CSV with an Arabic header on row 3, and a JSON container pointer
I10  storage: a same-size overwrite is 409 `source_changed`, an unreachable store is 503
     `storage_unavailable` for every route that reads, and nothing was stored
I11  no response carries an object key, a bucket, a storage URL or a credential

Requires SOVRA_DATABASE_URL (migration owner) and SOVRA_APP_DB_PASSWORD, and an S3 endpoint as in
test_ftf_0501_direct_upload_s3.py (this file boots the compose `s3` service when nothing answers).
Run: python3 tests/integration/test_ftf_h011_routes_postgres_s3.py
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
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from fastapi import FastAPI  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.auth.organization_workspace_schema import Organization, Workspace  # noqa: E402
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.datasets import direct_object_storage_upload as dos  # noqa: E402
from app.datasets.dataset_mapping_routes import build_dataset_mapping_router  # noqa: E402
from app.datasets.direct_object_storage_upload_routes import (  # noqa: E402
    build_direct_upload_router,
)
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402
from app.services.storage_semantics_client import S3Client, S3Endpoint  # noqa: E402
from app.tools.tool_definition_crud import ActorInfo, create_tool_definition  # noqa: E402

CID = "FTF-H011-P3"
FAILURES: list[str] = []
HERE = Path(__file__).resolve().parent
ISSUER = "https://issuer.example"


def _load(name: str, directory: Path = HERE) -> Any:
    spec = importlib.util.spec_from_file_location(name[:-3], directory / name)
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


def canonical_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()


class Principals:
    """Five principals per organization, one subject each: ml, admin, admin2, orgadmin, viewer."""

    ROLE_OF = {
        "ml": Role.ML_ENGINEER,
        "admin": Role.WORKSPACE_ADMIN,
        "admin2": Role.WORKSPACE_ADMIN,
        "orgadmin": Role.ORGANIZATION_ADMIN,
        "viewer": Role.VIEWER,
    }

    def __init__(self, org_ids: dict[str, uuid.UUID]) -> None:
        self.memberships: dict[tuple[str, str, uuid.UUID], Role] = {}
        for label, org_id in org_ids.items():
            for name, role in self.ROLE_OF.items():
                self.memberships[(ISSUER, f"user-{label}-{name}", org_id)] = role

    def principal_resolver(self, request: Any) -> AuthenticatedPrincipal | None:
        header = request.headers.get("x-test-principal")
        if header is None or not header.startswith("user-"):
            return None
        return AuthenticatedPrincipal(issuer=ISSUER, subject=header)

    def role_lookup(self, issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        return self.memberships.get((issuer, subject, organization_id))


def main() -> None:
    migration_url = p1._required_env("SOVRA_DATABASE_URL")
    app_password = p1._required_env("SOVRA_APP_DB_PASSWORD")

    # ---- I0: the migration chain ---------------------------------------------------------
    for args, label in (
        (["upgrade", "head"], "alembic upgrade head"),
        (["downgrade", "-1"], "alembic downgrade -1"),
        (["upgrade", "head"], "alembic upgrade head after downgrade -1"),
    ):
        result = p1._alembic(args, migration_url)
        check(f"I0 {label} ({result.stdout[-200:]}{result.stderr[-300:]})", result.returncode == 0)
        if result.returncode != 0:
            return _finish()
    heads = p1._alembic(["heads"], migration_url)
    check(
        f"I0 exactly one alembic head, 0036 ({heads.stdout!r})",
        heads.stdout.count("(head)") == 1 and "0036_dataset_mapping_requests" in heads.stdout,
    )

    endpoint, credentials = s3lab._load_endpoint_and_credentials()
    try:
        s3lab._wait_for_tcp(endpoint.host, endpoint.port, timeout_seconds=3.0)
    except TimeoutError:
        s3lab.compose("up", "-d", "s3")
        s3lab._wait_for_tcp(endpoint.host, endpoint.port, timeout_seconds=90.0)
    s3lab._wait_for_s3_http_ready(endpoint, timeout_seconds=90.0)
    client = S3Client(endpoint, credentials, timeout_seconds=60.0)
    bucket = f"sovra-ftfh011p3-{uuid.uuid4().hex[:12]}"
    client.create_bucket(bucket)

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(p1._app_role_url(migration_url, app_password))
    check(
        "I0 the app engine is the restricted sovra_app role", app_engine.url.username == "sovra_app"
    )
    factory = sessionmaker(bind=app_engine, expire_on_commit=False)

    with Session(owner_engine) as session:
        org1 = Organization(name="Acme H011P3", slug=f"acme-h011p3-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex H011P3", slug=f"globex-h011p3-{uuid.uuid4().hex[:8]}")
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

    def seed_connection(
        org: uuid.UUID,
        project_id: uuid.UUID,
        classification: str = "internal",
        purposes: tuple[str, ...] = ("training", "validation", "held_out_evaluation"),
    ) -> uuid.UUID:
        connection_id = uuid.uuid4()
        with owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO dataset_source_connections (id, organization_id, project_id, "
                    "source_type, display_name, allowed_purposes_json, security_classification, "
                    "connection_status, created_by) VALUES (:id, :org, :project, 'upload', "
                    ":name, CAST(:purposes AS jsonb), :classification, 'active', 'seed')"
                ),
                {
                    "id": connection_id,
                    "org": org,
                    "project": project_id,
                    "name": f"upload-{connection_id.hex[:6]}",
                    "purposes": json.dumps(list(purposes)),
                    "classification": classification,
                },
            )
        return connection_id

    conn_internal = seed_connection(org1_id, p1_id, "internal")
    conn_confidential = seed_connection(org1_id, p1_id, "confidential")
    conn_eval_only = seed_connection(org1_id, p1_id, "internal", ("held_out_evaluation",))
    conn_org2 = seed_connection(org2_id, p2_id, "internal")
    org_ids = {"org1": org1_id, "org2": org2_id}
    fixture = Principals(org_ids)
    storage_state: dict[str, Any] = {
        "settings": dos.UploadStorageSettings(endpoint, credentials, bucket)
    }

    app = FastAPI()
    app.include_router(
        build_direct_upload_router(
            factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
            storage_factory=lambda: storage_state["settings"],
            clock=lambda: datetime.now(UTC),
        )
    )
    app.include_router(
        build_dataset_mapping_router(
            factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
            storage_factory=lambda: storage_state["settings"],
        )
    )
    servers = contextlib.ExitStack()
    base = servers.enter_context(p1._running_app(app))
    seen_bodies: list[tuple[str, str]] = []

    def who(label: str, name: str) -> str:
        return f"user-{label}-{name}"

    def route(
        method: str,
        path: str,
        body: Any,
        principal: str,
        key: str | None = None,
    ) -> tuple[int, Any]:
        host, port = base.removeprefix("http://").split(":")
        headers = {"X-Test-Principal": principal}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if key is not None:
            headers["Idempotency-Key"] = key
        http_conn = http.client.HTTPConnection(host, int(port), timeout=120)
        try:
            http_conn.request(method, path, body=data, headers=headers)
            response = http_conn.getresponse()
            raw = response.read()
        finally:
            http_conn.close()
        seen_bodies.append((path, raw.decode("utf-8", "replace")))
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
        encoding: str | None = None,
    ) -> uuid.UUID:
        principal = who(org_label, "admin")
        collection = (
            f"/api/v1/organizations/{org_ids[org_label]}/projects/{project_id}/upload-sessions"
        )
        request: dict[str, Any] = {
            "dataset_source_connection_id": str(connection),
            "purpose": purpose,
            "declared_size_bytes": len(data),
            "declared_sha256": sha(data),
            "declared_content_type": content_type,
        }
        if encoding is not None:
            request["declared_encoding"] = encoding
        status, created = route("POST", collection, request, principal)
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
        return uuid.UUID(done["id"])

    def ds_path(org_label: str, project_id: uuid.UUID, dataset_id: uuid.UUID) -> str:
        return f"/api/v1/organizations/{org_ids[org_label]}/projects/{project_id}/datasets/{dataset_id}"

    def counts() -> tuple[int, int, int]:
        with owner_engine.connect() as conn:
            return (
                conn.execute(text("SELECT count(*) FROM dataset_mappings")).scalar_one(),
                conn.execute(text("SELECT count(*) FROM dataset_mapping_requests")).scalar_one(),
                conn.execute(
                    text(
                        "SELECT count(*) FROM audit_events WHERE resource_type = 'dataset_mapping'"
                    )
                ).scalar_one(),
            )

    def rows(sql: str, **params: Any) -> list[Any]:
        with owner_engine.connect() as conn:
            return list(conn.execute(text(sql), params).mappings())

    def msg_doc(role_map: dict[str, str] | None = None, **extra: Any) -> dict[str, Any]:
        document: dict[str, Any] = {
            "shape": "messages",
            "messages_pointer": "/conversation",
            "role_key": "role",
            "content_key": "content",
            "role_map": role_map or {"human": "user", "bot": "assistant"},
            "on_unmapped_message_keys": "ignore",
        }
        document.update(extra)
        return document

    def conversation(i: int, role: str = "human") -> bytes:
        return (
            json.dumps(
                {
                    "conversation": [
                        {"role": role, "content": f"مرحبا CANARY-RAW-{i}"},
                        {"role": "bot", "content": f"أهلا CANARY-REPLY-{i}"},
                    ],
                    "secret": f"CANARY-SECRET-{i}",
                },
                ensure_ascii=False,
            ).encode()
            + b"\n"
        )

    canaries = tuple(f"CANARY-{kind}-{i}" for kind in ("RAW", "REPLY", "SECRET") for i in range(4))

    # ---- I1: the happy path on a real Arabic JSONL dataset ---------------------------------
    train_data = b"".join(conversation(i) for i in range(3))
    train_id = make_dataset("org1", p1_id, conn_internal, train_data, "train")
    train = ds_path("org1", p1_id, train_id)
    status, probe = route("POST", f"{train}/mapping-probe", {}, who("org1", "ml"))
    check(
        f"I1 probe answers 200 completed (got {status})",
        status == 200 and probe["state"] == "completed",
    )
    check(
        "I1 the probe shows the real stored Arabic rows to an ml_engineer on an internal connection",
        probe["masked"] is False
        and len(probe["rows"]) == 3
        and probe["rows"][0]["conversation"][0]["content"] == "مرحبا CANARY-RAW-0",
    )
    document = msg_doc()
    status, preview = route(
        "POST", f"{train}/mapping-preview", {"mapping": document}, who("org1", "ml")
    )
    check(
        f"I1 preview answers 200 completed with 3 accepted rows (got {status})",
        status == 200 and preview["state"] == "completed" and preview["counts"]["accepted"] == 3,
    )
    check(
        "I1 the preview shows the raw row beside its canonical candidate",
        preview["rows"][0]["raw_row"]["conversation"][0]["role"] == "human"
        and preview["rows"][0]["candidate"] is not None
        and preview["rows"][0]["outcome"] == "accepted",
    )
    before = counts()
    status, proposed = route(
        "POST", f"{train}/mappings", {"mapping": document}, who("org1", "ml"), "i1-propose-0001"
    )
    check(
        f"I1 propose answers 201 (got {status} {proposed})",
        status == 201 and proposed["status"] == "proposed" and proposed["mapping_version"] == 1,
    )
    check(
        "I1 the proposal's preview digest is the preview route's",
        proposed["preview_sha256"] == preview["preview_sha256"],
    )
    check(
        "I1 one proposal wrote one mapping, one request and one audit event",
        counts() == (before[0] + 1, before[1] + 1, before[2] + 1),
    )
    mapping_id = proposed["id"]
    status, fetched = route("GET", f"{train}/mappings/{mapping_id}", None, who("org1", "ml"))
    check(
        "I1 the mapping reads back whole to the proposer",
        status == 200 and fetched["mapping_document"] == document and fetched["masked"] is False,
    )
    status, listing = route("GET", f"{train}/mappings", None, who("org1", "viewer"))
    check("I1 a viewer is refused (403)", status == 403)
    status, listing = route("GET", f"{train}/mappings?limit=1", None, who("org1", "orgadmin"))
    check(
        "I1 an organization_admin lists one version, next_after 1",
        status == 200
        and [m["mapping_version"] for m in listing["items"]] == [1]
        and listing["next_after"] == 1,
    )
    approve_request = {
        "expected_preview_sha256": proposed["preview_sha256"],
        "purpose": "training",
        "acknowledged_ignored_keys": [],
        "rationale": "roles checked against 3 real rows; the secret column stays unmapped",
    }
    status, bad = route(
        "POST",
        f"{train}/mappings/{mapping_id}/approve",
        approve_request,
        who("org1", "ml"),
        "i1-approve-0001",
    )
    check("I1 an ml_engineer cannot approve (403)", status == 403)
    before = counts()
    status, approved = route(
        "POST",
        f"{train}/mappings/{mapping_id}/approve",
        approve_request,
        who("org1", "admin"),
        "i1-approve-0001",
    )
    check(
        f"I1 an administrator approves (got {status} {approved})",
        status == 200 and approved["status"] == "approved" and approved["replayed"] is False,
    )
    check(
        "I1 the approver is the authenticated principal and the purpose the dataset's",
        approved["approved_by"] == who("org1", "admin")
        and approved["approver_role"] == "workspace_admin"
        and approved["approved_purpose"] == "training",
    )
    db = rows("SELECT * FROM dataset_mappings WHERE id = :id", id=uuid.UUID(mapping_id))[0]
    check(
        "I1 independently from the DB: approved, by the principal, mapping_sha256 recomputed",
        db["status"] == "approved"
        and db["approved_by"] == who("org1", "admin")
        and db["approved_purpose"] == "training"
        and db["mapping_sha256"] == canonical_sha(document)
        and db["mapping_document"] == document
        and db["dataset_id"] == train_id,
    )
    req_rows = rows(
        "SELECT * FROM dataset_mapping_requests WHERE mapping_id = :id ORDER BY created_at",
        id=uuid.UUID(mapping_id),
    )
    check(
        "I1 the propose and approve requests are stored with key, digest and the rationale",
        [r["action"] for r in req_rows] == ["propose", "approve"]
        and req_rows[0]["rationale"] is None
        and req_rows[1]["rationale"] == approve_request["rationale"]
        and req_rows[1]["idempotency_key"].startswith("i1-approve-")
        and all(len(r["request_digest"]) == 64 for r in req_rows),
    )
    audit = rows(
        "SELECT action, actor_id, details_json AS details FROM audit_events WHERE resource_type = 'dataset_mapping' ORDER BY created_at"
    )
    audit_text = json.dumps([dict(a) for a in audit], default=str, ensure_ascii=False)
    check(
        "I1 the audit trail names probed, previewed, proposed, approved, content-free",
        {
            "dataset_mapping.probed",
            "dataset_mapping.previewed",
            "dataset_mapping.proposed",
            "dataset_mapping.approved",
        }
        <= {a["action"] for a in audit}
        and not any(c in audit_text for c in canaries)
        and approve_request["rationale"] not in audit_text
        and "مرحبا" not in audit_text,
    )
    before = counts()
    status, replay = route(
        "POST",
        f"{train}/mappings/{mapping_id}/approve",
        approve_request,
        who("org1", "admin"),
        "i1-approve-0001",
    )
    check(
        "I1 the same approve request replays (200, replayed true) and writes nothing",
        status == 200 and replay["replayed"] is True and counts() == before,
    )
    status, elsewhere = route(
        "POST",
        f"{train}/mappings/{mapping_id}/approve",
        approve_request,
        who("org1", "admin"),
        "i1-approve-0009",
    )
    check(
        "I1 the same request under another key is recognised as already approved (replayed true) and writes nothing",
        status == 200 and elsewhere["replayed"] is True and counts() == before,
    )
    status, reuse = route(
        "POST",
        f"{train}/mappings/{mapping_id}/approve",
        {**approve_request, "rationale": "changed my mind"},
        who("org1", "admin"),
        "i1-approve-0001",
    )
    check(
        "I1 the same key with another rationale is 409 idempotency_key_reused",
        (status, reuse) == (409, {"detail": "idempotency_key_reused"}) and counts() == before,
    )
    status, other_admin = route(
        "POST",
        f"{train}/mappings/{mapping_id}/approve",
        approve_request,
        who("org1", "admin2"),
        "i1-approve-0003",
    )
    check(
        "I1 another administrator's approval of an approved mapping is 409 mapping_not_proposed",
        (status, other_admin) == (409, {"detail": "mapping_not_proposed"}),
    )
    status, rej = route(
        "POST",
        f"{train}/mappings/{mapping_id}/reject",
        {"rationale": "too late"},
        who("org1", "admin"),
        "i1-reject-0001",
    )
    check(
        "I1 an approved mapping cannot be rejected",
        (status, rej) == (409, {"detail": "mapping_not_proposed"}),
    )
    status, again = route(
        "POST", f"{train}/mappings", {"mapping": document}, who("org1", "ml"), "i1-propose-0002"
    )
    check(
        "I1 an unchanged re-proposal returns the latest version, writes no mapping (200)",
        status == 200 and again["id"] == mapping_id and again["created"] is False,
    )
    status, detail = route("GET", f"{train}/mappings/{mapping_id}", None, who("org1", "admin"))
    check(
        "I1 the approved mapping shows its decision with the rationale to an administrator",
        detail["decision"]
        == {
            "action": "approve",
            "rationale": approve_request["rationale"],
            "decided_by": who("org1", "admin"),
        },
    )

    # ---- I0b: raw sovra_app SQL --------------------------------------------------------------
    ctx1, ctx2 = TenantContext(organization_id=org1_id), TenantContext(organization_id=org2_id)
    mapping_uuid = uuid.UUID(mapping_id)

    def raw(ctx: TenantContext, sql: str, **params: Any) -> str | None:
        try:
            with tenant_scoped_session_for(factory, ctx) as session:
                session.execute(text(sql), params)
        except DBAPIError as exc:
            return type(exc.orig).__name__
        return None

    insert_sql = (
        "INSERT INTO dataset_mapping_requests (id, organization_id, project_id, mapping_id, action, "
        "idempotency_key, request_digest, rationale, created_by) VALUES (:id, :org, :project, :mapping, "
        ":action, :key, :digest, :rationale, 'raw')"
    )

    def insert(ctx: TenantContext, **kw: Any) -> str | None:
        params: dict[str, Any] = {
            "id": uuid.uuid4(),
            "org": ctx.organization_id,
            "project": p1_id,
            "mapping": mapping_uuid,
            "action": "approve",
            "key": f"raw-key-{uuid.uuid4().hex[:8]}",
            "digest": "a" * 64,
            "rationale": "raw",
        }
        params.update(kw)
        return raw(ctx, insert_sql, **params)

    check(
        "I0b a valid raw INSERT as sovra_app in the right tenant is accepted", insert(ctx1) is None
    )
    check(
        "I0b a cross-tenant INSERT is refused by row-level security",
        insert(ctx1, org=org2_id) is not None,
    )
    check(
        "I0b an INSERT under another tenant's context for this mapping is refused",
        insert(ctx2) is not None,
    )
    for label, patch in (
        ("an unknown action", {"action": "delete"}),
        ("an upper-case digest", {"digest": "A" * 64}),
        ("a short digest", {"digest": "a" * 63}),
        ("a short key", {"key": "short"}),
        ("a decision without a rationale", {"rationale": None}),
        ("an empty rationale", {"rationale": ""}),
        ("a rationale over 2000", {"rationale": "r" * 2001}),
        ("a proposal with a rationale", {"action": "propose", "rationale": "x"}),
        ("an unknown mapping", {"mapping": uuid.uuid4()}),
    ):
        check(
            f"I0b raw SQL with {label} is refused by the database",
            insert(ctx1, **patch) is not None,
        )
    check(
        "I0b a proposal without a rationale is accepted",
        insert(ctx1, action="propose", rationale=None) is None,
    )
    check("I0b a repeated key is refused", insert(ctx1, key="i1-approve-0001") is not None)
    check(
        "I0b sovra_app cannot UPDATE a stored request",
        raw(ctx1, "UPDATE dataset_mapping_requests SET rationale = 'x'") is not None,
    )
    check(
        "I0b sovra_app cannot DELETE a stored request",
        raw(ctx1, "DELETE FROM dataset_mapping_requests") is not None,
    )
    check(
        "I0b sovra_app cannot TRUNCATE the table",
        raw(ctx1, "TRUNCATE dataset_mapping_requests") is not None,
    )
    visible = rows(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'dataset_mapping_requests'"
    )[0]
    check(
        "I0b row-level security is ENABLED and FORCED on the table",
        visible["relrowsecurity"] and visible["relforcerowsecurity"],
    )
    policy = rows(
        "SELECT qual, with_check FROM pg_policies WHERE tablename = 'dataset_mapping_requests'"
    )
    check(
        "I0b exactly one non-allow-all tenant policy",
        len(policy) == 1
        and policy[0]["qual"] not in (None, "true")
        and policy[0]["with_check"] not in (None, "true"),
    )
    privileges = rows(
        "SELECT privilege_type FROM information_schema.role_table_grants WHERE grantee = 'sovra_app' AND table_name = 'dataset_mapping_requests' ORDER BY 1"
    )
    check(
        f"I0b sovra_app holds exactly INSERT and SELECT ({[p['privilege_type'] for p in privileges]})",
        [p["privilege_type"] for p in privileges] == ["INSERT", "SELECT"],
    )
    with tenant_scoped_session_for(factory, ctx2) as session:
        visible_to_org2 = session.execute(
            text("SELECT count(*) FROM dataset_mapping_requests")
        ).scalar_one()
    check("I0b another tenant sees none of the requests", visible_to_org2 == 0)

    # ---- I2: real tool definitions ------------------------------------------------------------
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
                    "properties": {"order_id": {"type": "string"}},
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
    tool_document = {
        "shape": "messages_json",
        "messages": {"key": "messages"},
        "role_key": "role",
        "content_key": "content",
        "tool_calls_key": "tool_calls",
        "tool_call_id_key": "tool_call_id",
        "name_key": "name",
        "role_map": {"user": "user", "assistant": "assistant", "tool": "tool"},
        "on_unmapped_message_keys": "reject",
    }

    def tool_row(call_name: str = "lookup_order", reply_id: str = "call_1") -> bytes:
        return (
            json.dumps(
                {
                    "messages": [
                        {"role": "user", "content": "where is it"},
                        {
                            "role": "assistant",
                            "content": "checking",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "name": call_name,
                                    "arguments": {"order_id": "abc"},
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

    tool_id = make_dataset(
        "org1",
        p1_id,
        conn_internal,
        tool_row() + tool_row(call_name="not_defined_anywhere") + tool_row(reply_id="call_orphan"),
        "tool-calls",
    )
    status, tool_preview = route(
        "POST",
        f"{ds_path('org1', p1_id, tool_id)}/mapping-preview",
        {"mapping": tool_document},
        who("org1", "admin"),
    )
    outcomes = [r["outcome"] for r in tool_preview["rows"]] if status == 200 else status
    check(
        f"I2 a valid tool call is accepted, an unknown tool and an orphan tool id are rejected rows ({outcomes})",
        outcomes == ["accepted", "rejected", "rejected"],
    )
    check(
        "I2 the rejected rows carry a machine-readable code",
        all(r["code"] for r in tool_preview["rows"][1:]),
    )
    tool_no_ids = {**tool_document}
    del tool_no_ids["tool_call_id_key"]
    before = counts()
    status, refusal = route(
        "POST",
        f"{ds_path('org1', p1_id, tool_id)}/mappings",
        {"mapping": tool_no_ids},
        who("org1", "admin"),
        "i2-propose-0001",
    )
    check(
        "I2 a tool role without tool_call_id_key is 400 tool_id_unmapped and stores nothing",
        (status, refusal) == (400, {"detail": "tool_id_unmapped"}) and counts() == before,
    )

    # ---- I2b: U+0000 would be a database error, so it is a 400 up front --------------------------
    before = counts()
    for nul_doc in (
        msg_doc(role_key="ro\u0000le"),
        msg_doc({"hu\u0000man": "user", "bot": "assistant"}),
    ):
        status, refused = route(
            "POST",
            f"{train}/mappings",
            {"mapping": nul_doc},
            who("org1", "admin"),
            "i2b-propose-0001",
        )
        check(
            f"I2b U+0000 in a mapping document is 400 mapping_invalid, never a 500 (got {status} {refused})",
            (status, refused) == (400, {"detail": "mapping_invalid"}),
        )
    check("I2b and nothing was stored", counts() == before)

    # ---- I3 / I4 -------------------------------------------------------------------------------
    before = counts()
    status, refusal = route(
        "POST",
        f"{train}/mappings",
        {"mapping": msg_doc({"human": "user"})},
        who("org1", "admin"),
        "i3-propose-0001",
    )
    check(
        "I3 no assistant in role_map is 400 critical_role_unmapped and stores nothing",
        (status, refusal) == (400, {"detail": "critical_role_unmapped"}) and counts() == before,
    )
    status, refusal = route(
        "POST",
        f"{train}/mapping-preview",
        {"mapping": msg_doc({"bot": "assistant"})},
        who("org1", "admin"),
    )
    check(
        "I3 the preview refuses it the same way",
        (status, refusal) == (400, {"detail": "critical_role_unmapped"}),
    )
    narrator_data = conversation(0) + conversation(1, role="narrator") + conversation(2)
    narrator_id = make_dataset("org1", p1_id, conn_internal, narrator_data, "narrator")
    narrator = ds_path("org1", p1_id, narrator_id)
    narrator_doc = msg_doc(on_unmapped_message_keys="reject")
    status, narrator_proposed = route(
        "POST",
        f"{narrator}/mappings",
        {"mapping": narrator_doc},
        who("org1", "admin"),
        "i4-propose-0001",
    )
    check(
        f"I4 a mapping with an unmapped raw role in the data is proposable (got {status})",
        status == 201 and narrator_proposed["preview_counts"].get("rejected:unmapped_role", 0) >= 1,
    )
    before = counts()
    status, refused = route(
        "POST",
        f"{narrator}/mappings/{narrator_proposed['id']}/approve",
        {
            "expected_preview_sha256": narrator_proposed["preview_sha256"],
            "purpose": "training",
            "acknowledged_ignored_keys": [],
            "rationale": "try",
        },
        who("org1", "admin"),
        "i4-approve-0001",
    )
    check(
        f"I4 but never approvable: 409 critical_rows_unmapped (got {status} {refused})",
        (status, refused) == (409, {"detail": "critical_rows_unmapped"}) and counts() == before,
    )
    still = rows(
        "SELECT status FROM dataset_mappings WHERE id = :id", id=uuid.UUID(narrator_proposed["id"])
    )[0]
    check("I4 and the mapping is still proposed", still["status"] == "proposed")
    status, rejected = route(
        "POST",
        f"{narrator}/mappings/{narrator_proposed['id']}/reject",
        {"rationale": "the narrator role is unmapped"},
        who("org1", "orgadmin"),
        "i4-reject-0001",
    )
    check(
        "I4 an organization_admin rejects it",
        status == 200
        and rejected["status"] == "rejected"
        and rejected["decision"]["rationale"] == "the narrator role is unmapped",
    )
    db_rej = rows(
        "SELECT approved_by, approved_purpose FROM dataset_mappings WHERE id = :id",
        id=uuid.UUID(narrator_proposed["id"]),
    )[0]
    check(
        "I4 a rejection records no approval",
        db_rej["approved_by"] is None and db_rej["approved_purpose"] is None,
    )

    # ---- I5: grants ------------------------------------------------------------------------------
    held_data = b"".join(conversation(i) for i in range(2))
    held_id = make_dataset(
        "org1", p1_id, conn_eval_only, held_data, "eval-only", purpose="held_out_evaluation"
    )
    held = ds_path("org1", p1_id, held_id)
    status, held_prop = route(
        "POST", f"{held}/mappings", {"mapping": msg_doc()}, who("org1", "admin"), "i5-propose-0001"
    )
    check(
        f"I5 an administrator maps a held-out dataset on an evaluation-only grant (got {status} {held_prop})",
        status == 201,
    )
    held_approval = {
        "expected_preview_sha256": held_prop["preview_sha256"],
        "purpose": "training",
        "acknowledged_ignored_keys": [],
        "rationale": "as training",
    }
    before = counts()
    status, refused = route(
        "POST",
        f"{held}/mappings/{held_prop['id']}/approve",
        held_approval,
        who("org1", "admin"),
        "i5-approve-0001",
    )
    check(
        f"I5 deny training for an evaluation-only grant: 403 purpose_not_authorized (got {status} {refused})",
        (status, refused) == (403, {"detail": "purpose_not_authorized"}) and counts() == before,
    )
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE dataset_source_connections SET allowed_purposes_json = CAST(:p AS jsonb) WHERE id = :id"
            ),
            {"p": json.dumps(["training", "held_out_evaluation"]), "id": conn_eval_only},
        )
    status, refused = route(
        "POST",
        f"{held}/mappings/{held_prop['id']}/approve",
        held_approval,
        who("org1", "admin"),
        "i5-approve-0002",
    )
    check(
        f"I5 a grant that allows training still cannot reclassify the dataset: 409 purpose_mismatch (got {status} {refused})",
        (status, refused) == (409, {"detail": "purpose_mismatch"}),
    )
    status, ok = route(
        "POST",
        f"{held}/mappings/{held_prop['id']}/approve",
        {**held_approval, "purpose": "held_out_evaluation"},
        who("org1", "admin"),
        "i5-approve-0003",
    )
    check(
        f"I5 the dataset's own purpose approves (got {status} {ok})",
        status == 200 and ok["approved_purpose"] == "held_out_evaluation",
    )
    narrowed_id = make_dataset("org1", p1_id, conn_internal, held_data, "narrowed")
    narrowed = ds_path("org1", p1_id, narrowed_id)
    status, narrowed_prop = route(
        "POST",
        f"{narrowed}/mappings",
        {"mapping": msg_doc()},
        who("org1", "admin"),
        "i5-propose-0002",
    )
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE dataset_source_connections SET allowed_purposes_json = CAST(:p AS jsonb) WHERE id = :id"
            ),
            {"p": json.dumps(["held_out_evaluation"]), "id": conn_internal},
        )
    before = counts()
    status, refused = route(
        "POST",
        f"{narrowed}/mappings/{narrowed_prop['id']}/approve",
        {
            "expected_preview_sha256": narrowed_prop["preview_sha256"],
            "purpose": "training",
            "acknowledged_ignored_keys": [],
            "rationale": "x",
        },
        who("org1", "admin"),
        "i5-approve-0004",
    )
    check(
        f"I5 a grant narrowed after the proposal denies the approval (got {status} {refused})",
        (status, refused) == (403, {"detail": "purpose_not_authorized"}) and counts() == before,
    )
    status, refused = route(
        "POST", f"{narrowed}/mapping-preview", {"mapping": msg_doc()}, who("org1", "admin")
    )
    check(
        "I5 and a new preview (no bytes are read before the grant is checked)",
        (status, refused) == (403, {"detail": "purpose_not_authorized"}),
    )
    status, refused = route("POST", f"{narrowed}/mapping-probe", {}, who("org1", "admin"))
    check("I5 and a new probe", (status, refused) == (403, {"detail": "purpose_not_authorized"}))
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE dataset_source_connections SET allowed_purposes_json = CAST(:p AS jsonb) WHERE id = :id"
            ),
            {
                "p": json.dumps(["training", "validation", "held_out_evaluation"]),
                "id": conn_internal,
            },
        )

    # ---- I6: masking -------------------------------------------------------------------------------
    secret_data = b"".join(conversation(i) for i in range(2))
    conf_id = make_dataset("org1", p1_id, conn_confidential, secret_data, "confidential")
    conf = ds_path("org1", p1_id, conf_id)
    status, conf_probe_ml = route("POST", f"{conf}/mapping-probe", {}, who("org1", "ml"))
    probe_text = json.dumps(conf_probe_ml, ensure_ascii=False)
    check(
        f"I6 an ml_engineer's probe of a confidential dataset is masked and shows no canary (got {status})",
        status == 200
        and conf_probe_ml["masked"] is True
        and not any(c in probe_text for c in canaries)
        and "مرحبا" not in probe_text
        and "conversation" not in probe_text,
    )
    status, conf_preview_ml = route(
        "POST", f"{conf}/mapping-preview", {"mapping": msg_doc()}, who("org1", "ml")
    )
    preview_text = json.dumps(conf_preview_ml, ensure_ascii=False)
    check(
        "I6 its preview is masked: no canary, no Arabic, no key name",
        status == 200
        and conf_preview_ml["masked"] is True
        and not any(c in preview_text for c in canaries)
        and "مرحبا" not in preview_text
        and "secret" not in preview_text,
    )
    check(
        "I6 the masked preview keeps counts and the approval digest",
        conf_preview_ml["counts"]["accepted"] == 2 and len(conf_preview_ml["preview_sha256"]) == 64,
    )
    status, conf_probe_admin = route("POST", f"{conf}/mapping-probe", {}, who("org1", "admin"))
    check(
        "I6 an administrator sees the real rows of the same dataset",
        conf_probe_admin["masked"] is False
        and "CANARY-RAW-0" in json.dumps(conf_probe_admin, ensure_ascii=False),
    )
    status, conf_prop = route(
        "POST", f"{conf}/mappings", {"mapping": msg_doc()}, who("org1", "ml"), "i6-propose-0001"
    )
    check(
        "I6 an ml_engineer's proposal on a confidential connection comes back with the document withheld",
        status == 201
        and conf_prop["masked"] is True
        and conf_prop["mapping_document"] is None
        and "conversation" not in json.dumps(conf_prop),
    )
    status, conf_list_ml = route("GET", f"{conf}/mappings", None, who("org1", "ml"))
    check(
        "I6 its list and get are masked too",
        all(m["masked"] and m["mapping_document"] is None for m in conf_list_ml["items"]),
    )
    status, conf_get_admin = route(
        "GET", f"{conf}/mappings/{conf_prop['id']}", None, who("org1", "admin")
    )
    check("I6 an administrator reads the document", conf_get_admin["mapping_document"] == msg_doc())
    status, rej = route(
        "POST",
        f"{conf}/mappings/{conf_prop['id']}/reject",
        {"rationale": "SECRET-RATIONALE-TEXT"},
        who("org1", "admin"),
        "i6-reject-0001",
    )
    status, conf_get_ml = route(
        "GET", f"{conf}/mappings/{conf_prop['id']}", None, who("org1", "ml")
    )
    check(
        "I6 the rejection rationale is withheld from the masked reader, the decision kept",
        conf_get_ml["decision"]
        == {"action": "reject", "rationale": None, "decided_by": who("org1", "admin")}
        and "SECRET-RATIONALE-TEXT" not in json.dumps(conf_get_ml),
    )
    status, internal_ml = route("POST", f"{train}/mapping-probe", {}, who("org1", "ml"))
    check(
        "I6 positive control: an ml_engineer sees real rows of an internal dataset",
        internal_ml["masked"] is False
        and "CANARY-RAW-0" in json.dumps(internal_ml, ensure_ascii=False),
    )

    # ---- I7: scoping -----------------------------------------------------------------------------------
    org2_data = b"".join(conversation(i) for i in range(2))
    org2_ds = make_dataset("org2", p2_id, conn_org2, org2_data, "org2-train")
    foreign = ds_path("org2", p2_id, org2_ds)
    for label, path in (
        ("an org1 path with org2's dataset id", f"{ds_path('org1', p1_id, org2_ds)}"),
        ("an org1 project with org2's dataset id", f"{ds_path('org1', p1b_id, org2_ds)}"),
        ("an org1 project that does not own the dataset", f"{ds_path('org1', p1b_id, train_id)}"),
    ):
        status, body = route("POST", f"{path}/mapping-probe", {}, who("org1", "admin"))
        check(
            f"I7 {label} is 404 not_found on probe (got {status})",
            (status, body) == (404, {"detail": "not_found"}),
        )
        status, body = route(
            "POST",
            f"{path}/mappings",
            {"mapping": msg_doc()},
            who("org1", "admin"),
            "i7-propose-0001",
        )
        check(
            f"I7 {label} is 404 not_found on propose",
            (status, body) == (404, {"detail": "not_found"}),
        )
        status, body = route("GET", f"{path}/mappings", None, who("org1", "admin"))
        check(
            f"I7 {label} is 404 not_found on list", (status, body) == (404, {"detail": "not_found"})
        )
    status, body = route("POST", f"{foreign}/mapping-probe", {}, who("org1", "admin"))
    check(f"I7 org2's path is refused to org1's principal (got {status})", status == 403)
    status, body = route(
        "GET",
        f"{ds_path('org1', p1_id, train_id)}/mappings/{narrator_proposed['id']}",
        None,
        who("org1", "admin"),
    )
    check(
        "I7 a mapping of another dataset read through this dataset's path is 404",
        (status, body) == (404, {"detail": "not_found"}),
    )
    status, body = route(
        "POST",
        f"{ds_path('org1', p1_id, train_id)}/mappings/{narrator_proposed['id']}/reject",
        {"rationale": "x"},
        who("org1", "admin"),
        "i7-reject-0001",
    )
    check(
        "I7 and it cannot be rejected through this dataset's path",
        (status, body) == (404, {"detail": "not_found"}),
    )
    status, body = route("GET", f"{held}/mappings", None, who("org1", "ml"))
    check(
        "I7 a held-out dataset's mappings are 404 to an ml_engineer",
        (status, body) == (404, {"detail": "not_found"}),
    )
    status, body = route(
        "POST", f"{held}/mapping-preview", {"mapping": msg_doc()}, who("org1", "ml")
    )
    check("I7 and its preview", (status, body) == (404, {"detail": "not_found"}))
    status, body = route(
        "POST", f"{held}/mappings", {"mapping": msg_doc()}, who("org1", "ml"), "i7-propose-0002"
    )
    check("I7 and its proposal", (status, body) == (404, {"detail": "not_found"}))
    status, body = route("GET", f"{held}/mappings", None, who("org1", "admin"))
    check("I7 while an administrator lists them", status == 200 and len(body["items"]) == 1)
    status, org2_prop = route(
        "POST",
        f"{foreign}/mappings",
        {"mapping": msg_doc()},
        who("org2", "admin"),
        "i7-propose-0003",
    )
    check("I7 org2's administrator maps org2's dataset", status == 201)
    with tenant_scoped_session_for(factory, ctx1) as session:
        cross = session.execute(
            text("SELECT count(*) FROM dataset_mappings WHERE dataset_id = :d"), {"d": org2_ds}
        ).scalar_one()
    check("I7 org1's tenant context sees none of org2's mappings", cross == 0)

    # ---- I8: concurrency -----------------------------------------------------------------------------------
    concurrent_id = make_dataset(
        "org1", p1_id, conn_internal, b"".join(conversation(i) for i in range(2)), "concurrent"
    )
    concurrent = ds_path("org1", p1_id, concurrent_id)
    results: list[tuple[int, Any]] = []
    lock = threading.Lock()

    def worker(fn: Any) -> None:
        got = fn()
        with lock:
            results.append(got)

    def run_all(fns: list[Any]) -> list[tuple[int, Any]]:
        results.clear()
        threads = [threading.Thread(target=worker, args=(fn,)) for fn in fns]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return list(results)

    before = counts()
    outcomes = run_all(
        [
            lambda: route(
                "POST",
                f"{concurrent}/mappings",
                {"mapping": msg_doc()},
                who("org1", "admin"),
                "i8-propose-0001",
            )
            for _ in range(12)
        ]
    )
    statuses = sorted(s for s, _ in outcomes)
    created = [b for s, b in outcomes if s == 201]
    check(
        f"I8 12 identical proposals: only 200/201/409 answers, exactly one 201 ({statuses})",
        set(statuses) <= {200, 201, 409} and len(created) == 1,
    )
    check(
        "I8 every 409 is a stable code",
        all(
            b["detail"] in ("mapping_conflict", "idempotency_key_reused")
            for s, b in outcomes
            if s == 409
        ),
    )
    check(
        "I8 exactly one mapping and one request row were written for that dataset",
        rows("SELECT count(*) AS n FROM dataset_mappings WHERE dataset_id = :d", d=concurrent_id)[
            0
        ]["n"]
        == 1
        and rows(
            "SELECT count(*) AS n FROM dataset_mapping_requests WHERE idempotency_key = 'i8-propose-0001'"
        )[0]["n"]
        == 1,
    )
    status, retry = route(
        "POST",
        f"{concurrent}/mappings",
        {"mapping": msg_doc()},
        who("org1", "admin"),
        "i8-propose-0001",
    )
    check(
        "I8 the retry of the same key replays the one mapping",
        status == 200 and retry["replayed"] is True and retry["id"] == created[0]["id"],
    )
    cm = created[0]
    approval = {
        "expected_preview_sha256": cm["preview_sha256"],
        "purpose": "training",
        "acknowledged_ignored_keys": [],
        "rationale": "concurrent approval",
    }
    outcomes = run_all(
        [
            lambda: route(
                "POST",
                f"{concurrent}/mappings/{cm['id']}/approve",
                approval,
                who("org1", "admin"),
                "i8-approve-0001",
            )
            for _ in range(8)
        ]
    )
    statuses = sorted(s for s, _ in outcomes)
    fresh = [b for s, b in outcomes if s == 200 and b["replayed"] is False]
    check(
        f"I8 8 identical approvals: all 200, exactly one is not a replay ({statuses}, {len(fresh)})",
        set(statuses) == {200} and len(fresh) == 1,
    )
    check(
        "I8 exactly one approve request row and one approver",
        rows(
            "SELECT count(*) AS n FROM dataset_mapping_requests WHERE mapping_id = :m AND action = 'approve'",
            m=uuid.UUID(cm["id"]),
        )[0]["n"]
        == 1,
    )
    race_id = make_dataset(
        "org1", p1_id, conn_internal, b"".join(conversation(i) for i in range(2)), "race"
    )
    race = ds_path("org1", p1_id, race_id)
    status, race_prop = route(
        "POST", f"{race}/mappings", {"mapping": msg_doc()}, who("org1", "admin"), "i8-propose-0002"
    )
    race_approve = {
        "expected_preview_sha256": race_prop["preview_sha256"],
        "purpose": "training",
        "acknowledged_ignored_keys": [],
        "rationale": "race",
    }
    outcomes = run_all(
        [
            lambda: route(
                "POST",
                f"{race}/mappings/{race_prop['id']}/approve",
                race_approve,
                who("org1", "admin"),
                "i8-approve-0002",
            )
        ]
        + [
            lambda: route(
                "POST",
                f"{race}/mappings/{race_prop['id']}/reject",
                {"rationale": "race"},
                who("org1", "orgadmin"),
                "i8-reject-0002",
            )
        ]
    )
    statuses = sorted(s for s, _ in outcomes)
    final = rows(
        "SELECT status FROM dataset_mappings WHERE id = :id", id=uuid.UUID(race_prop["id"])
    )[0]["status"]
    check(
        f"I8 approve vs reject: one wins, the other is 409 mapping_not_proposed ({statuses}, {final})",
        statuses == [200, 409]
        and final in ("approved", "rejected")
        and [b["detail"] for s, b in outcomes if s == 409] == ["mapping_not_proposed"],
    )
    decisions = rows(
        "SELECT action FROM dataset_mapping_requests WHERE mapping_id = :m AND action <> 'propose'",
        m=uuid.UUID(race_prop["id"]),
    )
    check(
        "I8 exactly one decision was stored",
        len(decisions) == 1
        and decisions[0]["action"] == ("approve" if final == "approved" else "reject"),
    )

    # ---- I9: windows-1256 CSV and a JSON container ----------------------------------------------------------------
    arabic_text = "ملاحظة;سطر\nأخرى;سطر\nسؤال;جواب;سري\nمرحبا;عالم;123\nمرحبا ثانية;عالم ثان;456\n"
    csv_id = make_dataset(
        "org1",
        p1_id,
        conn_internal,
        arabic_text.encode("cp1256"),
        "arabic-cp1256",
        content_type="text/csv",
        encoding="windows-1256",
    )
    csv_path = ds_path("org1", p1_id, csv_id)
    csv_options = {
        "csv": {"encoding": "windows-1256", "delimiter": ";", "has_header": True, "header_row": 2}
    }
    status, csv_probe = route(
        "POST", f"{csv_path}/mapping-probe", csv_options, who("org1", "admin")
    )
    check(
        f"I9 the probe reads the windows-1256 header on row 3 (got {status} {str(csv_probe)[:200]})",
        status == 200 and csv_probe["headers"] == ["سؤال", "جواب", "سري"],
    )
    csv_document = {
        "shape": "prompt_completion",
        **csv_options,
        "prompt": {"header": "سؤال"},
        "completion": {"header": "جواب"},
    }
    status, csv_prop = route(
        "POST",
        f"{csv_path}/mappings",
        {"mapping": csv_document},
        who("org1", "admin"),
        "i9-propose-0001",
    )
    check(
        f"I9 the CSV mapping is proposed (got {status} {csv_prop})",
        status == 201 and csv_prop["importer"] == "csv",
    )
    csv_approval = {
        "expected_preview_sha256": csv_prop["preview_sha256"],
        "purpose": "training",
        "acknowledged_ignored_keys": [],
        "rationale": "Arabic header on row 3",
    }
    status, csv_done = route(
        "POST",
        f"{csv_path}/mappings/{csv_prop['id']}/approve",
        csv_approval,
        who("org1", "admin"),
        "i9-approve-0001",
    )
    db_csv = rows(
        "SELECT mapping_sha256, mapping_document, importer FROM dataset_mappings WHERE id = :id",
        id=uuid.UUID(csv_prop["id"]),
    )[0]
    check(
        f"I9 the approved CSV mapping stores the exact document and digest (got {status})",
        status == 200
        and db_csv["mapping_sha256"] == canonical_sha(csv_document)
        and db_csv["mapping_document"] == csv_document
        and db_csv["importer"] == "csv",
    )
    json_doc = json.dumps(
        {"meta": {"v": 1}, "rows": [{"prompt": "p1", "completion": "c1"}, {"prompt": "p2"}]}
    ).encode()
    json_id = make_dataset(
        "org1", p1_id, conn_internal, json_doc, "json-container", content_type="application/json"
    )
    json_path = ds_path("org1", p1_id, json_id)
    json_document = {
        "shape": "prompt_completion",
        "json": {"container": "/rows", "ignored_top_level_keys": ["meta"]},
        "prompt": {"key": "prompt"},
        "completion": {"key": "completion"},
    }
    status, json_preview = route(
        "POST", f"{json_path}/mapping-preview", {"mapping": json_document}, who("org1", "admin")
    )
    check(
        f"I9 a JSON container pointer previews one accepted and one rejected row (got {status})",
        status == 200 and [r["outcome"] for r in json_preview["rows"]] == ["accepted", "rejected"],
    )
    status, json_probe = route(
        "POST",
        f"{json_path}/mapping-probe",
        {"json": {"container": "/rows", "ignored_top_level_keys": ["meta"]}},
        who("org1", "admin"),
    )
    check(
        f"I9 the JSON probe reads the container's rows (got {status})",
        status == 200 and len(json_probe["rows"]) == 2,
    )
    status, json_bad = route(
        "POST",
        f"{json_path}/mapping-probe",
        {"json": {"container": "no-slash", "ignored_top_level_keys": []}},
        who("org1", "admin"),
    )
    check(
        "I9 a bad container pointer is 400 mapping_invalid",
        (status, json_bad) == (400, {"detail": "mapping_invalid"}),
    )

    # ---- I10: storage ---------------------------------------------------------------------------------------------------
    same_data = b"".join(conversation(i % 4) for i in range(40))
    same_id = make_dataset("org1", p1_id, conn_internal, same_data, "same-size")
    same = ds_path("org1", p1_id, same_id)
    status, same_prop = route(
        "POST", f"{same}/mappings", {"mapping": msg_doc()}, who("org1", "admin"), "i10-propose-0001"
    )
    check("I10 fixture: the untouched dataset is proposable", status == 201)
    with tenant_scoped_session_for(factory, ctx1) as session:
        same_key = session.execute(
            text(
                "SELECT u.object_key FROM datasets d JOIN dataset_upload_sessions u ON u.id = d.upload_session_id WHERE d.id = :d"
            ),
            {"d": same_id},
        ).scalar_one()
    tampered = same_data.replace(b"CANARY-RAW-3", b"CANARY-RAW-9", 1)
    check(
        "I10 fixture: the tampered body is the same size and differs",
        len(tampered) == len(same_data) and tampered != same_data,
    )
    client.put_object(bucket, same_key, tampered)
    before = counts()
    status, changed = route(
        "POST", f"{same}/mapping-preview", {"mapping": msg_doc()}, who("org1", "admin")
    )
    check(
        f"I10 a same-size overwrite is 409 source_changed on preview (got {status} {changed})",
        (status, changed) == (409, {"detail": "source_changed"}),
    )
    status, changed = route("POST", f"{same}/mapping-probe", {}, who("org1", "admin"))
    check(
        f"I10 and on probe (got {status})", (status, changed) == (409, {"detail": "source_changed"})
    )
    status, changed = route(
        "POST",
        f"{same}/mappings/{same_prop['id']}/approve",
        {
            "expected_preview_sha256": same_prop["preview_sha256"],
            "purpose": "training",
            "acknowledged_ignored_keys": [],
            "rationale": "x",
        },
        who("org1", "admin"),
        "i10-approve-0001",
    )
    check(
        f"I10 and an approval is refused, never recorded (got {status} {changed})",
        status == 409
        and changed["detail"] in ("source_changed", "preview_changed")
        and rows(
            "SELECT status FROM dataset_mappings WHERE id = :id", id=uuid.UUID(same_prop["id"])
        )[0]["status"]
        == "proposed",
    )
    status, changed = route(
        "POST",
        f"{same}/mappings",
        {"mapping": msg_doc({"human": "user", "bot": "assistant", "sys": "system"})},
        who("org1", "admin"),
        "i10-propose-0002",
    )
    check(
        f"I10 and a new proposal (got {status} {changed})",
        (status, changed) == (409, {"detail": "source_changed"}),
    )
    live_settings = storage_state["settings"]
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    closed_port = closed.getsockname()[1]
    closed.close()
    storage_state["settings"] = dos.UploadStorageSettings(
        S3Endpoint(endpoint.scheme, "127.0.0.1", closed_port, endpoint.region), credentials, bucket
    )
    before = counts()
    try:
        outage_calls = (
            ("probe", "POST", f"{train}/mapping-probe", {}, None),
            ("preview", "POST", f"{train}/mapping-preview", {"mapping": document}, None),
            (
                "propose",
                "POST",
                f"{train}/mappings",
                {"mapping": msg_doc({"human": "user", "bot": "assistant", "sys": "system"})},
                "i10-outage-0001",
            ),
        )
        for label, method, path, body, key in outage_calls:
            status, outage = route(method, path, body, who("org1", "admin"), key)
            check(
                f"I10 an unreachable store is 503 storage_unavailable on {label} (got {status} {outage})",
                (status, outage) == (503, {"detail": "storage_unavailable"}),
            )
        check("I10 and nothing was stored", counts() == before)
        status, listing = route("GET", f"{train}/mappings", None, who("org1", "admin"))
        check("I10 reads of stored mappings do not need the store", status == 200)
        status, rej = route(
            "POST",
            f"{train}/mappings/{mapping_id}/reject",
            {"rationale": "x"},
            who("org1", "admin"),
            "i10-reject-0001",
        )
        check("I10 a rejection needs no storage (409: it is already approved)", status == 409)
    finally:
        storage_state["settings"] = live_settings
    status, recovered = route("POST", f"{train}/mapping-probe", {}, who("org1", "admin"))
    check(
        "I10 after the outage the same call works again",
        status == 200 and recovered["state"] == "completed",
    )

    # ---- I11: nothing internal in any response ---------------------------------------------------------------------------------------
    with owner_engine.connect() as conn:
        keys = [r[0] for r in conn.execute(text("SELECT object_key FROM dataset_upload_sessions"))]
    mapping_text = "\n".join(body for path, body in seen_bodies if "/mapping" in path)
    check("I11 the mapping routes were exercised (responses recorded)", len(mapping_text) > 10000)
    leaked = [k for k in keys if k and k in mapping_text]
    check(f"I11 no object key appears in any mapping response ({leaked[:2]})", not leaked)
    check("I11 no mapping response names the bucket", bucket not in mapping_text)
    check(
        "I11 no mapping response carries a storage credential or a presigned URL",
        credentials.secret_key not in mapping_text
        and credentials.access_key not in mapping_text
        and "X-Amz-Signature" not in mapping_text
        and f"{endpoint.host}:{endpoint.port}" not in mapping_text,
    )

    servers.close()
    return _finish()


def _finish() -> None:
    if FAILURES:
        print(f"FTF-H011-P3 routes integration: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-H011-P3 routes integration: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
