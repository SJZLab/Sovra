#!/usr/bin/env python3
"""FTF-0501 -- the real upload routes on a real uvicorn server, over PostgreSQL 16 as the
restricted `sovra_app` role, against the live SeaweedFS S3 API.

Neither earlier suite combines all three: `tests/tasks/test_ftf_0501.py` runs the routes over
SQLite and an in-process S3, and `test_ftf_0501_direct_upload_s3.py` runs the storage layer
without the routes or a database. This one drives `build_direct_upload_router` (the router
mounted in `apps/api/app/main.py`) end to end, so a Dataset version exists only after the
bytes in the real bucket were hashed and sniffed:

E1  happy path: create -> presigned part -> finalize -> 201, one Dataset, session verified
E2  wrong SHA-256 -> 422 sha256_mismatch, no Dataset, session rejected, object deleted
E3  partial upload -> 409 upload_incomplete, no Dataset, session stays open and resumes
E4  oversize part: the signed content-length refuses it; nothing stored; finalize incomplete
E5  wrong key: a grant cannot write another session's key or part
E6  expiry -> 410 upload_expired, no Dataset, session expired
E7  concurrent finalize of one session -> exactly one 201, one Dataset
E8  cross-tenant: another organization's principal gets 404/403 everywhere, and raw
    `sovra_app` SQL under another tenant sees no rows
E9  content type from the bytes: a docx declared XLSX and PK-prefixed junk -> 422
    content_type_mismatch; a real spreadsheet package -> 201
E10 roles: viewer 403, ml_engineer may not open a held_out_evaluation upload, no principal 401

Every refusal checks "no Dataset was created" and, where bytes were stored, "the object is
gone" in the real bucket. Requires SOVRA_DATABASE_URL (migration owner) and
SOVRA_APP_DB_PASSWORD, and an S3 endpoint as in test_ftf_0501_direct_upload_s3.py (this file
boots the compose `s3` service when nothing answers).
Run: python3 tests/integration/test_ftf_0501_routes_postgres_s3.py
"""

from __future__ import annotations

import hashlib
import http.client
import importlib.util
import json
import sys
import threading
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from fastapi import FastAPI  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.auth.organization_workspace_schema import Organization, Workspace  # noqa: E402
from app.auth.rbac_middleware import Role  # noqa: E402
from app.datasets import direct_object_storage_upload as dos  # noqa: E402
from app.datasets.direct_object_storage_upload_routes import (  # noqa: E402
    build_direct_upload_router,
)
from app.projects.project_schema import Project  # noqa: E402
from app.services.storage_semantics_client import S3Client, S3Error  # noqa: E402

CID = "FTF-0501-E"
FAILURES: list[str] = []
HERE = Path(__file__).resolve().parent


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


class Resp:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body)

    def detail(self) -> Any:
        try:
            return self.json().get("detail")
        except (ValueError, AttributeError):
            return None


def call(base: str, method: str, path: str, body: Any = None, principal: str | None = None) -> Resp:
    headers = {"X-Test-Principal": principal} if principal else {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=60)
    try:
        conn.request(method, path, body=data, headers=headers)
        response = conn.getresponse()
        return Resp(response.status, response.read())
    finally:
        conn.close()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def jsonl(size: int) -> bytes:
    line = b'{"prompt":"hello","completion":"world"}\n'
    return (line * (size // len(line) + 1))[:size]


def zip_of(members: list[tuple[str, bytes]]) -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            archive.writestr(name, data)
    return buffer.getvalue()


SHEET = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
WORD = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"


def types_xml(part: str, content_type: str) -> bytes:
    return (
        '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/'
        f'content-types"><Override PartName="{part}" ContentType="{content_type}"/></Types>'
    ).encode()


def main() -> None:
    migration_url = p1._required_env("SOVRA_DATABASE_URL")
    app_password = p1._required_env("SOVRA_APP_DB_PASSWORD")
    for args in (["upgrade", "head"],):
        result = p1._alembic(args, migration_url)
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
    bucket = f"sovra-ftf0501r-{uuid.uuid4().hex[:12]}"
    client.create_bucket(bucket)
    settings = dos.UploadStorageSettings(endpoint, credentials, bucket)

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(p1._app_role_url(migration_url, app_password))
    check("the app engine is the restricted sovra_app role", app_engine.url.username == "sovra_app")
    factory = sessionmaker(bind=app_engine, expire_on_commit=False)

    with Session(owner_engine) as session:
        org1 = Organization(name="Acme E", slug=f"acme-e-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex E", slug=f"globex-e-{uuid.uuid4().hex[:8]}")
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

    def seed_connection(org: uuid.UUID, project_id: uuid.UUID, purposes: list[str]) -> uuid.UUID:
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
                    "purposes": json.dumps(purposes),
                },
            )
        return connection_id

    conn1 = seed_connection(org1_id, p1_id, ["training", "held_out_evaluation"])
    conn2 = seed_connection(org2_id, p2_id, ["training"])

    fixture = p1._Fixture({"org1": org1_id, "org2": org2_id})
    clock_offset = {"value": timedelta(0)}

    def clock() -> datetime:
        return datetime.now(UTC) + clock_offset["value"]

    app = FastAPI()
    app.include_router(
        build_direct_upload_router(
            factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
            storage_factory=lambda: settings,
            clock=clock,
        )
    )
    ml = fixture.header_for("org1", Role.ML_ENGINEER)
    admin = fixture.header_for("org1", Role.WORKSPACE_ADMIN)
    viewer = fixture.header_for("org1", Role.VIEWER)
    other_ml = fixture.header_for("org2", Role.ML_ENGINEER)

    def counts() -> tuple[int, int]:
        with owner_engine.connect() as conn:
            sessions = conn.execute(text("SELECT count(*) FROM dataset_upload_sessions"))
            datasets = conn.execute(text("SELECT count(*) FROM datasets"))
            return int(sessions.scalar() or 0), int(datasets.scalar() or 0)

    def datasets_total() -> int:
        return counts()[1]

    def session_status(session_id: str) -> str | None:
        with owner_engine.connect() as conn:
            row = conn.execute(
                text("SELECT status FROM dataset_upload_sessions WHERE id = :id"),
                {"id": uuid.UUID(session_id)},
            ).first()
        return None if row is None else str(row[0])

    def object_present(key: str) -> bool:
        try:
            client.head_object(bucket, key)
        except S3Error as exc:
            if exc.status_code == 404:
                return False
            raise
        return True

    with p1._running_app(app) as base:
        col1 = f"/api/v1/organizations/{org1_id}/projects/{p1_id}/upload-sessions"
        col2 = f"/api/v1/organizations/{org2_id}/projects/{p2_id}/upload-sessions"

        def create(
            body: bytes,
            *,
            content_type: str = "application/jsonl",
            declared_sha: str | None = None,
            declared_size: int | None = None,
            purpose: str = "training",
            principal: str | None = ml,
            connection: uuid.UUID = conn1,
            path: str = col1,
        ) -> Resp:
            return call(
                base,
                "POST",
                path,
                {
                    "dataset_source_connection_id": str(connection),
                    "purpose": purpose,
                    "declared_size_bytes": len(body) if declared_size is None else declared_size,
                    "declared_sha256": declared_sha or sha(body),
                    "declared_content_type": content_type,
                },
                principal,
            )

        def object_key(session_id: str) -> str:
            with owner_engine.connect() as conn:
                row = conn.execute(
                    text("SELECT object_key FROM dataset_upload_sessions WHERE id = :id"),
                    {"id": uuid.UUID(session_id)},
                ).first()
            assert row is not None
            return str(row[0])

        def grants(session_id: str, numbers: list[int], principal: str | None = ml) -> list[Any]:
            got = call(
                base, "POST", f"{col1}/{session_id}/part-urls", {"part_numbers": numbers}, principal
            )
            check(f"part-urls answers 200 (got {got.status} {got.detail()})", got.status == 200)
            return got.json()["parts"] if got.status == 200 else []

        def upload_all(session_id: str, body: bytes) -> list[int]:
            statuses = []
            for grant in grants(session_id, [1]):
                statuses.append(s3lab._put(grant["url"], body))
            return statuses

        def finalize(session_id: str, name: str = "ds", principal: str | None = ml) -> Resp:
            return call(
                base, "POST", f"{col1}/{session_id}/finalize", {"dataset_name": name}, principal
            )

        # ---- E1 happy path ---------------------------------------------------
        body = jsonl(200_000)
        snap = counts()
        made = create(body)
        check(f"E1 create answers 201 (got {made.status} {made.detail()})", made.status == 201)
        sid = made.json()["id"]
        check("E1 a new session is open", made.json()["status"] == "open")
        check("E1 no storage field in the create response", "object_key" not in made.json())
        check("E1 the real server accepts the presigned PUT", upload_all(sid, body) == [200])
        done = finalize(sid, "happy")
        check(f"E1 finalize answers 201 (got {done.status} {done.detail()})", done.status == 201)
        check("E1 exactly one Dataset exists", datasets_total() == snap[1] + 1)
        check("E1 the session is verified", session_status(sid) == "verified")
        check("E1 the verified object stays in the bucket", object_present(object_key(sid)))
        check(
            "E1 finalize again is 409 and creates nothing",
            finalize(sid, "happy-again").status == 409 and datasets_total() == snap[1] + 1,
        )

        # ---- E2 wrong hash ---------------------------------------------------
        before = datasets_total()
        wrong = create(body, declared_sha=sha(b"some other bytes"))
        sid2 = wrong.json()["id"]
        upload_all(sid2, body)
        key2 = object_key(sid2)
        got = finalize(sid2, "wrong-hash")
        check(
            f"E2 wrong hash -> 422 sha256_mismatch (got {got.status} {got.detail()})",
            got.status == 422 and got.detail() == "sha256_mismatch",
        )
        check("E2 no Dataset was created", datasets_total() == before)
        check("E2 the session is rejected", session_status(sid2) == "rejected")
        check("E2 the stored object is deleted", not object_present(key2))

        # ---- E3 partial upload, then resume -----------------------------------
        before = datasets_total()
        made3 = create(body)
        sid3 = made3.json()["id"]
        got = finalize(sid3, "partial")
        check(
            f"E3 nothing uploaded -> 409 upload_incomplete (got {got.status} {got.detail()})",
            got.status == 409 and got.detail() == "upload_incomplete",
        )
        check("E3 no Dataset was created", datasets_total() == before)
        check("E3 the session stays open", session_status(sid3) == "open")
        listed = call(base, "GET", f"{col1}/{sid3}/parts", None, ml)
        check("E3 no part is listed yet", listed.status == 200 and listed.json()["parts"] == [])
        check("E3 the upload resumes", upload_all(sid3, body) == [200])
        resumed = finalize(sid3, "resumed")
        check(f"E3 resumed finalize -> 201 (got {resumed.status})", resumed.status == 201)
        check("E3 exactly one Dataset after the resume", datasets_total() == before + 1)

        # ---- E4 oversize part is refused by the signature --------------------
        before = datasets_total()
        made4 = create(body)
        sid4 = made4.json()["id"]
        key4 = object_key(sid4)
        grant = grants(sid4, [1])[0]
        check(
            "E4 a part one byte too long is refused",
            s3lab._refused(s3lab._put(grant["url"], body + b"x")),
        )
        check(
            "E4 a part one byte short is refused",
            s3lab._refused(s3lab._put(grant["url"], body[:-1])),
        )
        listed = call(base, "GET", f"{col1}/{sid4}/parts", None, ml)
        check("E4 nothing was stored", listed.status == 200 and listed.json()["parts"] == [])
        got = finalize(sid4, "oversize")
        check(
            f"E4 finalize is 409 upload_incomplete (got {got.status} {got.detail()})",
            got.status == 409 and got.detail() == "upload_incomplete",
        )
        check("E4 no Dataset was created", datasets_total() == before)
        check("E4 the object is not in the bucket", not object_present(key4))

        # ---- E5 wrong key / wrong part ----------------------------------------
        other_made = create(body)
        sid5 = other_made.json()["id"]
        grant5 = grants(sid5, [1])[0]
        key5 = object_key(sid5)
        check(
            "E5 a grant cannot write another key",
            s3lab._refused(s3lab._put(grant5["url"].replace(key5, key5 + "x"), body)),
        )
        check(
            "E5 a grant cannot write another part number",
            s3lab._refused(s3lab._put(grant5["url"].replace("partNumber=1", "partNumber=2"), body)),
        )
        check(
            "E5 a grant cannot write session 4's key",
            s3lab._refused(s3lab._put(grant5["url"].replace(key5, key4), body)),
        )
        check(
            "E5 nothing is stored under either key",
            not object_present(key4) and not object_present(key5),
        )
        before = datasets_total()
        got = finalize(sid5, "wrong-key")
        check(
            "E5 finalize is 409 upload_incomplete", got.status == 409 and datasets_total() == before
        )

        # ---- E6 expiry --------------------------------------------------------
        before = datasets_total()
        made6 = create(body)
        sid6 = made6.json()["id"]
        upload_all(sid6, body)
        clock_offset["value"] = timedelta(hours=25)
        try:
            got = finalize(sid6, "expired")
            late_urls = call(base, "POST", f"{col1}/{sid6}/part-urls", {"part_numbers": [1]}, ml)
        finally:
            clock_offset["value"] = timedelta(0)
        check(
            f"E6 finalize after expiry -> 410 upload_expired (got {got.status} {got.detail()})",
            got.status == 410 and got.detail() == "upload_expired",
        )
        check("E6 no Dataset was created", datasets_total() == before)
        check("E6 the session is expired", session_status(sid6) == "expired")
        check(
            "E6 an expired session issues no more part URLs",
            late_urls.status in (409, 410) and "url" not in late_urls.body.decode(),
        )

        # ---- E7 concurrent finalize --------------------------------------------
        before = datasets_total()
        made7 = create(body)
        sid7 = made7.json()["id"]
        upload_all(sid7, body)
        results: list[Resp] = []
        barrier = threading.Barrier(4)

        def worker(index: int) -> None:
            barrier.wait()
            results.append(finalize(sid7, f"concurrent-{index}"))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        statuses = sorted(r.status for r in results)
        check(f"E7 four finalizers answered ({statuses})", len(results) == 4)
        check(f"E7 exactly one 201 ({statuses})", statuses.count(201) == 1)
        check(f"E7 every other answer is 409 ({statuses})", statuses.count(409) == 3)
        check("E7 exactly one Dataset was created", datasets_total() == before + 1)

        # ---- E8 cross-tenant ---------------------------------------------------
        made8 = create(body)
        sid8 = made8.json()["id"]
        snap = counts()
        foreign_read = call(base, "GET", f"{col1}/{sid8}", None, other_ml)
        check(
            f"E8 another organization's principal cannot read the session ({foreign_read.status})",
            foreign_read.status in (403, 404),
        )
        via_own = call(base, "GET", f"{col2}/{sid8}", None, other_ml)
        check("E8 through its own organization the id is a 404", via_own.status == 404)
        check(
            "E8 that 404 is identical to a random id's",
            via_own.body == call(base, "GET", f"{col2}/{uuid.uuid4()}", None, other_ml).body,
        )
        check(
            "E8 another organization cannot mint part URLs",
            call(base, "POST", f"{col1}/{sid8}/part-urls", {"part_numbers": [1]}, other_ml).status
            in (403, 404),
        )
        check(
            "E8 another organization cannot finalize",
            call(base, "POST", f"{col1}/{sid8}/finalize", {"dataset_name": "x"}, other_ml).status
            in (403, 404),
        )
        check(
            "E8 another organization cannot open a session on our connection",
            create(body, principal=other_ml, connection=conn1, path=col2).status in (403, 404),
        )
        check(
            "E8 our principal cannot use the other organization's connection",
            create(body, connection=conn2).status in (403, 404),
        )
        check(
            "E8 none of those refusals created a Dataset",
            datasets_total() == snap[1] and counts()[0] == snap[0],
        )
        with factory() as raw:
            raw.execute(
                text("SELECT set_config('app.current_organization_id', :org, false)"),
                {"org": str(org2_id)},
            )
            seen = raw.execute(
                text("SELECT count(*) FROM dataset_upload_sessions WHERE id = :id"),
                {"id": uuid.UUID(sid8)},
            ).scalar()
            own_org2 = raw.execute(text("SELECT count(*) FROM dataset_upload_sessions")).scalar()
        check("E8 raw sovra_app SQL under org2 does not see org1's session", seen == 0)
        check("E8 raw sovra_app SQL under org2 sees no org1 rows at all", own_org2 == 0)
        with factory() as raw:
            raw.execute(
                text("SELECT set_config('app.current_organization_id', :org, false)"),
                {"org": str(org1_id)},
            )
            seen = raw.execute(
                text("SELECT count(*) FROM dataset_upload_sessions WHERE id = :id"),
                {"id": uuid.UUID(sid8)},
            ).scalar()
        check("E8 positive control: raw sovra_app SQL under org1 sees it", seen == 1)

        # ---- E9 content type comes from the bytes -------------------------------
        xlsx_type = dos.XLSX_CONTENT_TYPE
        docx = zip_of(
            [
                ("[Content_Types].xml", types_xml("/word/document.xml", WORD)),
                ("word/document.xml", b"<document/>"),
            ]
        )
        junk = b"PK\x03\x04" + b"junk" * 100
        for label, payload in (("a docx", docx), ("PK-prefixed junk", junk)):
            before = datasets_total()
            made9 = create(payload, content_type=xlsx_type)
            sid9 = made9.json()["id"]
            upload_all(sid9, payload)
            key9 = object_key(sid9)
            got = finalize(sid9, "not-a-sheet")
            check(
                f"E9 {label} declared XLSX -> 422 content_type_mismatch "
                f"({got.status} {got.detail()})",
                got.status == 422 and got.detail() == "content_type_mismatch",
            )
            check(f"E9 ({label}) no Dataset was created", datasets_total() == before)
            check(f"E9 ({label}) the object is deleted", not object_present(key9))
        good = zip_of(
            [
                ("[Content_Types].xml", types_xml("/xl/workbook.xml", SHEET)),
                ("xl/workbook.xml", b"<workbook/>"),
            ]
        )
        before = datasets_total()
        made9 = create(good, content_type=xlsx_type)
        sid9 = made9.json()["id"]
        upload_all(sid9, good)
        got = finalize(sid9, "sheet")
        check(
            f"E9 a real spreadsheet package -> 201 (got {got.status} {got.detail()})",
            got.status == 201,
        )
        check("E9 exactly one Dataset for the spreadsheet", datasets_total() == before + 1)

        # ---- E10 roles ----------------------------------------------------------
        snap = counts()
        check("E10 no principal -> 401", create(body, principal=None).status == 401)
        check("E10 a viewer -> 403", create(body, principal=viewer).status == 403)
        got = create(body, purpose="held_out_evaluation", principal=ml)
        check(
            f"E10 ml_engineer opening held_out_evaluation -> 403 purpose_forbidden ({got.status})",
            got.status == 403 and got.detail() == "purpose_forbidden",
        )
        admin_made = create(body, purpose="held_out_evaluation", principal=admin)
        check(f"E10 workspace_admin may open it ({admin_made.status})", admin_made.status == 201)
        sid10 = admin_made.json()["id"]
        hidden = call(base, "GET", f"{col1}/{sid10}", None, ml)
        check(
            "E10 ml_engineer reads that session as a 404 identical to a random id's",
            hidden.status == 404
            and hidden.body == call(base, "GET", f"{col1}/{uuid.uuid4()}", None, ml).body,
        )
        check(
            "E10 only the admin's session was created",
            counts()[0] == snap[0] + 1 and datasets_total() == snap[1],
        )

    app_engine.dispose()
    owner_engine.dispose()
    if booted_here:
        s3lab.compose("stop", "s3", check_exit=False)
    _finish()


def _finish() -> None:
    if FAILURES:
        print(f"FTF-0501 real routes (PG16 + SeaweedFS): {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0501 real routes (PG16 + SeaweedFS): all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
