#!/usr/bin/env python3
"""FTF-0501 round 3 -- the abandoned-upload sweeper (Section 67.5, 24 h) against the live
SeaweedFS S3 API and PostgreSQL 16 as the restricted `sovra_app` role.

Before this suite the sweeper was proven only against an in-process fake that returned an
`Initiated` time. The real SeaweedFS (3.99) lists no `Initiated`, so every upload was
"undated", nothing was aborted, and the run exited 0; and the listing was one unmarked page.

W1  the real listing carries no `Initiated` (the premise); without a session lookup the
    sweeper aborts nothing, reports the uploads as undated and `main()` exits 1
W2  an upload is dated by its session's own `expires_at` (read as `sovra_app` under the key's
    tenant): an expired session's upload is aborted, a live one is kept, a foreign key and an
    upload of a session that does not exist are left alone
W3  an expired upload behind more than 1000 others (several listing pages, real markers) is
    reached and aborted; every other upload survives
W4  `main()` through the real environment: exit 1 while an undatable upload exists, exit 0
    once the listing is fully dated and nothing is left to abort
W5  tenant isolation: the lookup, running as `sovra_app`, cannot read another organization's
    session expiry (it answers None)

Requires SOVRA_DATABASE_URL (migration owner), SOVRA_APP_DB_PASSWORD and an S3 endpoint as in
test_ftf_0501_direct_upload_s3.py (this file boots the compose `s3` service when nothing
answers). Run: python3 tests/integration/test_ftf_0501_sweeper_postgres_s3.py
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.auth.organization_workspace_schema import Organization, Workspace  # noqa: E402
from app.datasets import direct_object_storage_upload as dos  # noqa: E402
from app.datasets import upload_sweeper as sweeper  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402
from app.services.storage_semantics_client import S3Client  # noqa: E402

CID = "FTF-0501-W"
FAILURES: list[str] = []
HERE = Path(__file__).resolve().parent
FILLERS = 1005  # more than one full listing page of 1000


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


def _all_uploads(client: S3Client, bucket: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    markers: tuple[str, str] | None = ("", "")
    while markers is not None:
        page, markers = client.list_multipart_uploads_page(
            bucket, prefix="", key_marker=markers[0], upload_id_marker=markers[1]
        )
        found.extend(page)
    return found


def main() -> None:
    migration_url = p1._required_env("SOVRA_DATABASE_URL")
    app_password = p1._required_env("SOVRA_APP_DB_PASSWORD")
    result = p1._alembic(["upgrade", "head"], migration_url)
    check(f"alembic upgrade head ({result.stdout}{result.stderr})", result.returncode == 0)
    if result.returncode != 0:
        return _finish()
    app_url = p1._app_role_url(migration_url, app_password)

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
    bucket = f"sovra-ftf0501w-{uuid.uuid4().hex[:12]}"
    client.create_bucket(bucket)
    settings = dos.UploadStorageSettings(endpoint, credentials, bucket)

    owner_engine = create_engine(migration_url)
    with Session(owner_engine) as session:
        orgs = []
        for label in ("Acme W", "Globex W"):
            org = Organization(name=label, slug=f"{label[:4].lower()}-w-{uuid.uuid4().hex[:8]}")
            session.add(org)
            session.flush()
            session.add(Workspace(organization_id=org.id, name="Main", slug="main"))
            session.flush()
            ws = session.query(Workspace).filter_by(organization_id=org.id).one()
            proj = Project(
                organization_id=org.id,
                workspace_id=ws.id,
                name="p",
                project_type="generic_model_tuning",
                status="active",
                security_profile="standard",
                row_version=1,
                created_by="seed",
            )
            session.add(proj)
            session.flush()
            orgs.append((org.id, proj.id))
        session.commit()
    (org1, project1), (org2, project2) = orgs

    def seed_connection(org: uuid.UUID, project: uuid.UUID) -> uuid.UUID:
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
                    "project": project,
                    "name": f"upload-{connection_id.hex[:6]}",
                    "purposes": json.dumps(["training"]),
                },
            )
        return connection_id

    conn1 = seed_connection(org1, project1)
    seed_connection(org2, project2)

    def insert_sessions(rows: list[tuple[uuid.UUID, timedelta]]) -> None:
        """Open upload sessions of organization 1 expiring `ttl` after the database clock."""
        with owner_engine.begin() as conn:
            now: datetime = conn.execute(text("SELECT now()")).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO dataset_upload_sessions (id, organization_id, project_id, "
                    "dataset_source_connection_id, purpose, object_key, declared_size_bytes, "
                    "max_bytes, declared_sha256, declared_content_type, status, created_by, "
                    "created_at, expires_at, updated_at) VALUES (:id, :org, :project, :conn, "
                    "'training', :key, 500, 1000, :sha, 'application/jsonl', 'open', 'seed', "
                    ":created, :expires, :created)"
                ),
                [
                    {
                        "id": sid,
                        "org": org1,
                        "project": project1,
                        "conn": conn1,
                        "key": key_of(sid),
                        "sha": "9" * 64,
                        "created": now,
                        "expires": now + ttl,
                    }
                    for sid, ttl in rows
                ],
            )

    def key_of(session_id: uuid.UUID) -> str:
        return f"tenants/{org1}/projects/{project1}/uploads/{session_id}/source"

    def open_upload(key: str) -> str:
        return client.create_multipart_upload(bucket, key, content_type="application/jsonl")

    def alive() -> set[str]:
        return {u["UploadId"] for u in _all_uploads(client, bucket)}

    database_lookup, dispose = sweeper._database_expiry_lookup(app_url)

    # ---- W1 the real listing has no Initiated; no lookup -> nothing aborted ---------------
    expired_id, live_id, missing_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    insert_sessions([(expired_id, timedelta(hours=1)), (live_id, timedelta(hours=24))])
    expired_up = open_upload(key_of(expired_id))
    live_up = open_upload(key_of(live_id))
    orphan_up = open_upload(key_of(missing_id))  # no such session row
    foreign_key = f"tenants/{org1}/not-a-session"
    foreign_up = open_upload(foreign_key)
    later = datetime.now(UTC) + timedelta(hours=2)  # the 1 h session is over, the 24 h one is not
    listing = _all_uploads(client, bucket)
    has_initiated = any(u.get("Initiated") for u in listing)
    undated_run = sweeper.sweep_abandoned_uploads(client, settings, now=later)
    if not has_initiated:
        check(
            f"W1 without a session lookup nothing is aborted and all four are undated "
            f"({undated_run})",
            undated_run.aborted == 0 and undated_run.undated == 3 and undated_run.foreign == 1,
        )
        check("W1 every upload is still in the bucket", alive() >= {expired_up, live_up})

    # ---- W2 dated by the session's own expiry ---------------------------------------------
    run = sweeper.sweep_abandoned_uploads(
        client, settings, now=later, expiry_lookup=database_lookup
    )
    left = alive()
    check(f"W2 exactly one upload (the expired session's) is aborted ({run})", run.aborted == 1)
    check("W2 the expired session's upload is gone", expired_up not in left)
    check(
        "W2 the live session's, the orphan's and the foreign key's uploads are untouched",
        {live_up, orphan_up, foreign_up} <= left,
    )
    if not has_initiated:
        check(
            "W2 the upload of a session that does not exist is undated, never aborted",
            run.undated == 1 and run.kept == 1,
        )
    # the live one is kept now, aborted once its own deadline has passed
    run = sweeper.sweep_abandoned_uploads(
        client, settings, now=datetime.now(UTC) + timedelta(hours=25), expiry_lookup=database_lookup
    )
    check("W2 25 h later the live session's upload is aborted", live_up not in alive())
    check("W2 and the foreign / orphan uploads are still untouched", {foreign_up} <= alive())

    # ---- W3 an expired upload behind more than 1000 others -------------------------------
    last_id = uuid.UUID(
        "ffffffff" + uuid.uuid4().hex[8:]
    )  # sorts after every filler (first nibble < f)
    filler_ids = [uuid.UUID(f"{i % 14:x}{uuid.uuid4().hex[1:]}") for i in range(FILLERS)]
    insert_sessions([(sid, timedelta(hours=24)) for sid in filler_ids])
    insert_sessions([(last_id, timedelta(hours=1))])
    filler_uploads = [open_upload(key_of(sid)) for sid in filler_ids]
    last_up = open_upload(key_of(last_id))
    run = sweeper.sweep_abandoned_uploads(
        client, settings, now=datetime.now(UTC) + timedelta(hours=2), expiry_lookup=database_lookup
    )
    everything = _all_uploads(client, bucket)
    left = {u["UploadId"] for u in everything}
    survivors = left & set(filler_uploads)
    # Diagnostics for the real server's paging behaviour (printed on every run).
    _, _, raw = client._request(
        "GET", bucket, query={"uploads": "", "prefix": "tenants/", "max-uploads": "1000"}
    )
    head = raw.decode("utf-8", "replace")
    print(
        f"W3 diagnostics: run={run}; listing total={len(everything)} unique={len(left)}; "
        f"fillers surviving={len(survivors)}/{len(filler_uploads)}; first page raw head="
        f"{head[:300]!r} ... tail={head[-400:]!r}"
    )
    check(f"W3 the listing took more than one page ({run})", run.pages >= 2)
    check("W3 the expired upload behind 1005 others is aborted", last_up not in left)
    check(
        f"W3 all 1005 live uploads survive ({len(survivors)} do; aborted={run.aborted})",
        set(filler_uploads) <= left and run.aborted == 1,
    )
    check(
        f"W3 the sweep saw the whole listing ({run})",
        not run.more_may_remain and run.listed >= FILLERS,
    )

    # ---- W4 main() through the real environment --------------------------------------------
    saved = {
        name: os.environ.get(name)
        for name in (
            "SOVRA_UPLOAD_S3_ENDPOINT",
            "SOVRA_UPLOAD_S3_BUCKET",
            "SOVRA_UPLOAD_S3_ACCESS_KEY",
            "SOVRA_UPLOAD_S3_SECRET_KEY",
            "SOVRA_UPLOAD_S3_REGION",
            "SOVRA_DATABASE_URL",
        )
    }
    try:
        os.environ["SOVRA_UPLOAD_S3_ENDPOINT"] = (
            f"{endpoint.scheme}://{endpoint.host}:{endpoint.port}"
        )
        os.environ["SOVRA_UPLOAD_S3_BUCKET"] = bucket
        os.environ["SOVRA_UPLOAD_S3_ACCESS_KEY"] = credentials.access_key
        os.environ["SOVRA_UPLOAD_S3_SECRET_KEY"] = credentials.secret_key
        os.environ["SOVRA_UPLOAD_S3_REGION"] = endpoint.region
        os.environ["SOVRA_DATABASE_URL"] = app_url
        sink = io.StringIO()
        with redirect_stdout(sink), redirect_stderr(sink):
            code_with_orphan = sweeper.main()
        if not has_initiated:
            check(
                f"W4 an upload whose age cannot be derived makes main() exit 1 ({sink.getvalue()})",
                code_with_orphan == 1,
            )
        client.abort_multipart_upload(bucket, key_of(missing_id), orphan_up)
        client.abort_multipart_upload(bucket, foreign_key, foreign_up)
        with redirect_stdout(sink), redirect_stderr(sink):
            code_clean = sweeper.main()
        check(f"W4 a fully dated listing exits 0 ({sink.getvalue()[-300:]})", code_clean == 0)
        check("W4 and the live uploads are still there", set(filler_uploads) <= alive())
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    # ---- W5 the lookup, as sovra_app, cannot read another tenant's session -----------------
    check(
        "W5 the lookup answers None for a session read under another organization",
        database_lookup(org2, filler_ids[0]) is None,
    )
    check(
        "W5 and the expiry for its own organization is a real timestamp",
        isinstance(database_lookup(org1, filler_ids[0]), datetime),
    )

    dispose()
    owner_engine.dispose()
    if booted_here:
        s3lab.compose("stop", "s3", check_exit=False)
    _finish()


def _finish() -> None:
    if FAILURES:
        print(f"FTF-0501 sweeper (PG16 + SeaweedFS): {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0501 sweeper (PG16 + SeaweedFS): all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
