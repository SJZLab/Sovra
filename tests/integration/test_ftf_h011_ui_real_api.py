#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,assignment,call-arg,index,misc,union-attr,no-any-return,var-annotated"
# ruff: noqa: E501
"""FTF-H011-P4 (UI CHECKLIST f) -- the real dataset mapping wizard against the REAL FTF-H011-P3 routes.

`apps/web/tests/ftf_h011.spec.ts` drives the component against a route-table `fetch`. This suite
boots `build_direct_upload_router` and `build_dataset_mapping_router` (as mounted in
`apps/api/app/main.py`) on a real uvicorn server over PostgreSQL 16 as the restricted `sovra_app`
role and the live SeaweedFS S3 API, uploads every dataset through the real FTF-0501 routes, then
runs `apps/web/tests/real_api/ftf_h011_ui_real_api.realapi.ts` (Vitest + jsdom, the real component
and real `fetch`) against it:

R1  a windows-1256 `;` CSV with an Arabic header on row 3 -> approved
R2  JSONL with tool calls, an orphan tool id and an unknown tool -> rejected rows shown
R3  no assistant in `role_map` -> 400 shown, nothing stored
R4  an unmapped raw role -> proposable, approve 409 shown
R5  an evaluation-only grant -> 403 shown; a grant narrowed after the proposal -> 403 shown; a
    held-out dataset on its evaluation-only grant is approved for its own purpose only
R6  ml_engineer on a confidential connection sees only masked values (canary scan of the DOM and
    every response)
R7  a foreign organization's dataset -> 404 shown
R8  storage stopped -> blocked shown, then readable again
R9  JSON with a container pointer

Afterwards this file checks the database independently of the browser side: R1's approved row has
the exact `mapping_sha256` of the document the UI built (and that document), R3 stored nothing,
R4 is still proposed, R5's narrowed proposal is still proposed and R5c is approved as
`held_out_evaluation`, R6/R7/R8 stored nothing, no row of any mapping table belongs to the other
organization, and no canary appears in any stored mapping, request or audit row.

The harness's `/__harness/storage-down|storage-up|narrow-r5b` routes stand in for an operator
stopping the object store or narrowing a grant between two UI steps, and are labelled as such.

Requires SOVRA_DATABASE_URL (migration owner), SOVRA_APP_DB_PASSWORD, S3 as in
test_ftf_0501_direct_upload_s3.py (this file boots the compose `s3` service when nothing answers),
and Node with `pnpm install` done.
Run: python3 tests/integration/test_ftf_h011_ui_real_api.py
"""

from __future__ import annotations

import hashlib
import http.client
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from fastapi import FastAPI, Response  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
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

CID = "FTF-H011-P4-UI-F"
FAILURES: list[str] = []
HERE = Path(__file__).resolve().parent
ISSUER = "https://issuer.example"
CANARIES = ["CANARY-H011UI-RAW", "CANARY-H011UI-SECRET", "CANARY-H011UI-REPLY"]


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


def canonical_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()


class Principals:
    ROLE_OF = {"ml": Role.ML_ENGINEER, "admin": Role.WORKSPACE_ADMIN}

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


def conversation(i: int, *, extra_role: str | None = None) -> bytes:
    turns = [
        {"role": "human", "content": f"مرحبا {CANARIES[0]}-{i}"},
        {"role": "bot", "content": f"أهلا {CANARIES[2]}-{i}"},
    ]
    if extra_role is not None:
        turns.append({"role": extra_role, "content": "aside"})
    return (
        json.dumps(
            {"conversation": turns, "secret": f"{CANARIES[1]}-{i}"}, ensure_ascii=False
        ).encode()
        + b"\n"
    )


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
                            {"id": "call_1", "name": call_name, "arguments": {"order_id": "abc"}}
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
    bucket = f"sovra-ftfh011ui-{uuid.uuid4().hex[:12]}"
    client.create_bucket(bucket)
    live = dos.UploadStorageSettings(endpoint, credentials, bucket)
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    closed_port = closed.getsockname()[1]
    closed.close()
    down = dos.UploadStorageSettings(
        S3Endpoint(endpoint.scheme, "127.0.0.1", closed_port, endpoint.region), credentials, bucket
    )
    storage: dict[str, Any] = {"settings": live}

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(p1._app_role_url(migration_url, app_password))
    check("the app engine is the restricted sovra_app role", app_engine.url.username == "sovra_app")
    factory = sessionmaker(bind=app_engine, expire_on_commit=False)

    with Session(owner_engine) as session:
        org1 = Organization(name="Acme H011UI", slug=f"acme-h011ui-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex H011UI", slug=f"globex-h011ui-{uuid.uuid4().hex[:8]}")
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

        proj1, proj2 = project(org1, ws1, "p1"), project(org2, ws2, "p2")
        session.add_all([proj1, proj2])
        session.commit()
        org1_id, org2_id, p1_id, p2_id = org1.id, org2.id, proj1.id, proj2.id
    org_ids = {"org1": org1_id, "org2": org2_id}
    ctx1 = TenantContext(organization_id=org1_id)

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

    def set_purposes(connection: uuid.UUID, purposes: list[str]) -> None:
        with owner_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE dataset_source_connections SET allowed_purposes_json = CAST(:p AS jsonb) WHERE id = :id"
                ),
                {"p": json.dumps(purposes), "id": connection},
            )

    fixture = Principals(org_ids)
    app = FastAPI()
    app.include_router(
        build_direct_upload_router(
            factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
            storage_factory=lambda: storage["settings"],
            clock=lambda: datetime.now(UTC),
        )
    )
    app.include_router(
        build_dataset_mapping_router(
            factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
            storage_factory=lambda: storage["settings"],
        )
    )
    harness_calls: list[str] = []

    # Harness-only operator actions (labelled; not product routes).
    @app.post("/__harness/storage-down")
    def _storage_down() -> Response:
        harness_calls.append("storage-down")
        storage["settings"] = down
        return Response(status_code=204)

    @app.post("/__harness/storage-up")
    def _storage_up() -> Response:
        harness_calls.append("storage-up")
        storage["settings"] = live
        return Response(status_code=204)

    conn_internal = seed_connection(org1_id, p1_id)
    conn_confidential = seed_connection(org1_id, p1_id, "confidential")
    conn_eval_only = seed_connection(org1_id, p1_id, purposes=("held_out_evaluation",))
    conn_r5a = seed_connection(org1_id, p1_id)
    conn_r5b = seed_connection(org1_id, p1_id)
    conn_org2 = seed_connection(org2_id, p2_id)

    @app.post("/__harness/narrow-r5b")
    def _narrow() -> Response:
        harness_calls.append("narrow-r5b")
        set_purposes(conn_r5b, ["held_out_evaluation"])
        return Response(status_code=204)

    with p1._running_app(app) as base:
        host, port = base.removeprefix("http://").split(":")

        def route(method: str, path: str, body: Any, principal: str) -> tuple[int, Any]:
            headers = {"X-Test-Principal": principal}
            data = None
            if body is not None:
                data = json.dumps(body).encode()
                headers["Content-Type"] = "application/json"
            conn = http.client.HTTPConnection(host, int(port), timeout=120)
            try:
                conn.request(method, path, body=data, headers=headers)
                response = conn.getresponse()
                raw = response.read()
            finally:
                conn.close()
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
            principal = f"user-{org_label}-admin"
            collection = (
                f"/api/v1/organizations/{org_ids[org_label]}/projects/{project_id}/upload-sessions"
            )
            request: dict[str, Any] = {
                "dataset_source_connection_id": str(connection),
                "purpose": purpose,
                "declared_size_bytes": len(data),
                "declared_sha256": hashlib.sha256(data).hexdigest(),
                "declared_content_type": content_type,
            }
            if encoding is not None:
                request["declared_encoding"] = encoding
            status, created = route("POST", collection, request, principal)
            check(
                f"fixture {name}: create session answers 201 (got {status} {created})",
                status == 201,
            )
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
                check(f"fixture {name}: part {number} PUT", s3lab._put(grant["url"], piece) == 200)
            status, done = route(
                "POST", f"{collection}/{session_id}/finalize", {"dataset_name": name}, principal
            )
            check(f"fixture {name}: finalize answers 201 (got {status} {done})", status == 201)
            return uuid.UUID(done["id"])

        with tenant_scoped_session_for(factory, ctx1) as session:
            create_tool_definition(
                session,
                ctx1,
                project_id=p1_id,
                name="lookup_order",
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

        arabic_csv = (
            "ملاحظة;سطر\nأخرى;سطر\nسؤال;جواب;سري\nمرحبا;عالم;123\nمرحبا ثانية;عالم ثان;456\n"
        )
        chats = b"".join(conversation(i) for i in range(3))
        datasets = {
            "r1": make_dataset(
                "org1",
                p1_id,
                conn_internal,
                arabic_csv.encode("cp1256"),
                "r1-arabic-cp1256",
                content_type="text/csv",
                encoding="windows-1256",
            ),
            "r2": make_dataset(
                "org1",
                p1_id,
                conn_internal,
                tool_row()
                + tool_row(call_name="not_defined_anywhere")
                + tool_row(reply_id="call_orphan"),
                "r2-tools",
            ),
            "r3": make_dataset("org1", p1_id, conn_internal, chats, "r3-no-assistant"),
            "r4": make_dataset(
                "org1",
                p1_id,
                conn_internal,
                chats + conversation(9, extra_role="narrator"),
                "r4-unmapped-role",
            ),
            "r5a": make_dataset("org1", p1_id, conn_r5a, chats, "r5a-eval-only"),
            "r5b": make_dataset("org1", p1_id, conn_r5b, chats, "r5b-narrowed"),
            "r5c": make_dataset(
                "org1", p1_id, conn_eval_only, chats, "r5c-held-out", purpose="held_out_evaluation"
            ),
            "r6": make_dataset("org1", p1_id, conn_confidential, chats, "r6-confidential"),
            "r7": make_dataset("org2", p2_id, conn_org2, chats, "r7-foreign"),
            "r8": make_dataset("org1", p1_id, conn_internal, chats, "r8-storage"),
            "r9": make_dataset(
                "org1",
                p1_id,
                conn_internal,
                json.dumps(
                    {
                        "meta": {"v": 1},
                        "rows": [{"prompt": "p1", "completion": "c1"}, {"prompt": "p2"}],
                    }
                ).encode(),
                "r9-json-container",
                content_type="application/json",
            ),
        }
        # R5a: the grant is evaluation-only before the user starts (the upload itself needed training).
        set_purposes(conn_r5a, ["held_out_evaluation"])

        env = dict(os.environ)
        env.update(
            {
                "FTF_H011_UI_API_BASE": base,
                "FTF_H011_UI_SEEDS": json.dumps(
                    {
                        "org": str(org1_id),
                        "project": str(p1_id),
                        "datasets": {k: str(v) for k, v in datasets.items()},
                        "admin": "user-org1-admin",
                        "ml": "user-org1-ml",
                        "canaries": CANARIES,
                    }
                ),
            }
        )
        ran = subprocess.run(
            [
                "pnpm",
                "exec",
                "vitest",
                "run",
                "--reporter=verbose",
                "--config",
                "vitest.real-api.config.ts",
                "ftf_h011_ui_real_api",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=1200,
        )
        print(ran.stdout[-8000:])
        print(ran.stderr[-3000:])
        check(f"the Vitest real-API run exits 0 (got {ran.returncode})", ran.returncode == 0)
        plain = re.sub(r"\x1b\[[0-9;]*m", "", ran.stdout)
        for scenario in ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9"):
            check(
                f"the Vitest run executed and passed {scenario}",
                re.search(rf"✓.*\b{scenario} real ", plain) is not None
                and re.search(rf"[×✗].*\b{scenario} real ", plain) is None,
            )
        check(
            f"the harness actions ran in order ({harness_calls})",
            harness_calls == ["narrow-r5b", "storage-down", "storage-up"],
        )

    # ---- independent database verification ----------------------------------------------------
    def rows(sql: str, **params: Any) -> list[Any]:
        with owner_engine.connect() as conn:
            return list(conn.execute(text(sql), params).mappings())

    def mappings_of(name: str) -> list[Any]:
        return rows(
            "SELECT status, mapping_sha256, mapping_document, approved_purpose, approved_by, importer "
            "FROM dataset_mappings WHERE dataset_id = :d ORDER BY mapping_version",
            d=datasets[name],
        )

    r1_document = {
        "shape": "prompt_completion",
        "prompt": {"header": "سؤال"},
        "completion": {"header": "جواب"},
        "csv": {"encoding": "windows-1256", "delimiter": ";", "has_header": True, "header_row": 2},
    }
    r1 = mappings_of("r1")
    check(
        f"R1 exactly one approved mapping ({[r['status'] for r in r1]})",
        [r["status"] for r in r1] == ["approved"],
    )
    if r1:
        check(
            "R1 the stored document is the one the UI built",
            r1[0]["mapping_document"] == r1_document,
        )
        check(
            "R1 the stored mapping_sha256 is the exact digest of that document",
            r1[0]["mapping_sha256"] == canonical_sha(r1_document),
        )
        check(
            "R1 approved for training by the admin, csv importer",
            (r1[0]["approved_purpose"], r1[0]["approved_by"], r1[0]["importer"])
            == ("training", "user-org1-admin", "csv"),
        )
    check("R2 nothing was proposed from a preview", mappings_of("r2") == [])
    check("R3 nothing stored after the 400", mappings_of("r3") == [])
    r4 = mappings_of("r4")
    check(
        f"R4 proposed and never approved ({[r['status'] for r in r4]})",
        [r["status"] for r in r4] == ["proposed"],
    )
    check("R5a nothing stored under the evaluation-only grant", mappings_of("r5a") == [])
    r5b = mappings_of("r5b")
    check(
        f"R5b the narrowed proposal is still proposed ({[r['status'] for r in r5b]})",
        [r["status"] for r in r5b] == ["proposed"],
    )
    r5c = mappings_of("r5c")
    check(
        f"R5c approved for held_out_evaluation only ({[(r['status'], r['approved_purpose']) for r in r5c]})",
        [(r["status"], r["approved_purpose"]) for r in r5c]
        == [("approved", "held_out_evaluation")],
    )
    for name in ("r6", "r7", "r8", "r9"):
        check(f"{name.upper()} nothing stored", mappings_of(name) == [])
    org2_rows = rows(
        "SELECT (SELECT count(*) FROM dataset_mappings WHERE organization_id = :o) AS m, "
        "(SELECT count(*) FROM dataset_mapping_requests WHERE organization_id = :o) AS r",
        o=org2_id,
    )[0]
    check(
        f"no mapping row in the other organization ({dict(org2_rows)})",
        (org2_rows["m"], org2_rows["r"]) == (0, 0),
    )
    requests = rows(
        "SELECT action, count(*) AS n FROM dataset_mapping_requests WHERE organization_id = :o GROUP BY action",
        o=org1_id,
    )
    by_action = {r["action"]: r["n"] for r in requests}
    check(
        f"requests: 4 proposals and 2 approvals were recorded ({by_action})",
        by_action == {"propose": 4, "approve": 2},
    )
    stored = json.dumps(
        [
            [
                dict(r)
                for r in rows(
                    "SELECT mapping_document::text AS d, preview_counts::text AS c FROM dataset_mappings"
                )
            ],
            [dict(r) for r in rows("SELECT rationale FROM dataset_mapping_requests")],
            [
                dict(r)
                for r in rows(
                    "SELECT details_json::text AS d FROM audit_events WHERE resource_type = 'dataset_mapping'"
                )
            ],
        ],
        ensure_ascii=False,
        default=str,
    )
    for canary in CANARIES:
        check(
            f"canary {canary} is in no stored mapping, request or audit row", canary not in stored
        )

    app_engine.dispose()
    owner_engine.dispose()
    if booted_here:
        s3lab.compose("stop", "s3", check_exit=False)
    _finish()


def _finish() -> None:
    if FAILURES:
        print(f"FTF-H011-P4 UI real API (PG16 + SeaweedFS): {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-H011-P4 UI real API (PG16 + SeaweedFS): all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
