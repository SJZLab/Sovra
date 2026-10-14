#!/usr/bin/env python3
"""FTF-0501-UI -- the real resumable-upload component against the REAL FTF-0501 router.

`apps/web/tests/ftf_0501_ui.spec.ts` drives the component against a mocked `fetch`. This suite
closes the UI CHECKLIST's item (f): it boots `build_direct_upload_router` (as mounted in
`apps/api/app/main.py`) on a real uvicorn server over PostgreSQL 16 as the restricted
`sovra_app` role against the live SeaweedFS S3 API (part size lowered to S3's 5 MiB minimum so
an 11 MiB file is three real parts), then runs
`apps/web/tests/real_api/ftf_0501_ui_real_api.realapi.ts` (Vitest + jsdom, the real component and
real `FileReader` / `crypto.subtle` / `fetch`) against it:

R1  multi-part upload -> finalize -> "Dataset created." with a Dataset bound to the session
R2  connection drop on part 2, page reload, resume: ONLY the missing parts are requested and
    uploaded (part 1 never re-sent), then an accepted Dataset
R3  another organization's path is refused by the server; nothing is created

Afterwards this file checks the database and the bucket independently of the browser side:
exactly two Datasets, each with the SHA-256 / size of the bytes the UI picked, both sessions
verified, no row in the other organization, and the stored objects' sizes.

Requires SOVRA_DATABASE_URL (migration owner), SOVRA_APP_DB_PASSWORD, S3 as in
test_ftf_0501_direct_upload_s3.py (this file boots the compose `s3` service when nothing
answers), and Node with `pnpm install` done.
Run: python3 tests/integration/test_ftf_0501_ui_real_api.py
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import subprocess
import sys
import uuid
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

CID = "FTF-0501-UI-F"
FAILURES: list[str] = []
HERE = Path(__file__).resolve().parent
FILE_BYTES = 11 * 1024 * 1024  # three parts at the 5 MiB S3 minimum: 5 + 5 + 1 MiB


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


def jsonl_bytes(seed: int, size: int) -> bytes:
    """The same deterministic JSONL the Vitest side generates (1024-byte lines)."""
    line = b'{"k":"' + chr(97 + seed).encode() * 1015 + b'"}\n'
    assert len(line) == 1024 and size % 1024 == 0
    return line * (size // 1024)


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
    client = S3Client(endpoint, credentials, timeout_seconds=120.0)
    bucket = f"sovra-ftf0501ui-{uuid.uuid4().hex[:12]}"
    client.create_bucket(bucket)
    settings = dos.UploadStorageSettings(endpoint, credentials, bucket)

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(p1._app_role_url(migration_url, app_password))
    check("the app engine is the restricted sovra_app role", app_engine.url.username == "sovra_app")
    factory = sessionmaker(bind=app_engine, expire_on_commit=False)

    with Session(owner_engine) as session:
        org1 = Organization(name="Acme UI", slug=f"acme-ui-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex UI", slug=f"globex-ui-{uuid.uuid4().hex[:8]}")
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
        org1_id, org2_id, p1_id = org1.id, org2.id, proj1.id

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
                "org": org1_id,
                "project": p1_id,
                "name": f"upload-{connection_id.hex[:6]}",
                "purposes": '["training"]',
            },
        )

    fixture = p1._Fixture({"org1": org1_id, "org2": org2_id})
    app = FastAPI()
    app.include_router(
        build_direct_upload_router(
            factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
            storage_factory=lambda: settings,
        )
    )
    principal = fixture.header_for("org1", Role.ML_ENGINEER)

    # A real multi-part layout needs parts of at least S3's 5 MiB minimum; the server reads the
    # default at call time.
    dos.DEFAULT_PART_BYTES = dos.MIN_PART_BYTES

    with p1._running_app(app) as base:
        env = dict(os.environ)
        env.update(
            {
                "FTF_0501_UI_API_BASE": base,
                "FTF_0501_UI_ORG": str(org1_id),
                "FTF_0501_UI_OTHER_ORG": str(org2_id),
                "FTF_0501_UI_PROJECT": str(p1_id),
                "FTF_0501_UI_CONNECTION": str(connection_id),
                "FTF_0501_UI_PRINCIPAL": principal,
                "FTF_0501_UI_FILE_BYTES": str(FILE_BYTES),
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
                "ftf_0501_ui_real_api",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
        )
        print(ran.stdout[-6000:])
        print(ran.stderr[-3000:])
        check(f"the Vitest real-API run exits 0 (got {ran.returncode})", ran.returncode == 0)
        # CI forces colour: strip ANSI escapes before looking for the scenario names.
        plain = re.sub(r"\x1b\[[0-9;]*m", "", ran.stdout)
        for scenario in ("R1", "R2", "R3"):
            check(
                f"the Vitest run executed and passed {scenario}",
                re.search(rf"✓.*\b{scenario} real ", plain) is not None,
            )

    # ---- independent database / bucket verification -------------------------------------
    expected = {
        "ui-real-r1": jsonl_bytes(0, FILE_BYTES),
        "ui-real-r2": jsonl_bytes(1, FILE_BYTES),
    }
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT d.name, d.verified_sha256, d.verified_size_bytes, d.organization_id, "
                "d.project_id, s.status, s.object_key FROM datasets d "
                "JOIN dataset_upload_sessions s ON s.id = d.upload_session_id "
                "WHERE d.organization_id = :o"
            ),
            {"o": org1_id},
        ).all()
        org2_sessions = conn.execute(
            text("SELECT count(*) FROM dataset_upload_sessions WHERE organization_id = :o"),
            {"o": org2_id},
        ).scalar()
        all_sessions = conn.execute(
            text("SELECT count(*) FROM dataset_upload_sessions WHERE organization_id = :o"),
            {"o": org1_id},
        ).scalar()
        org2_datasets = conn.execute(
            text("SELECT count(*) FROM datasets WHERE organization_id = :o"), {"o": org2_id}
        ).scalar()
    check(f"exactly the two Datasets the UI created exist (got {len(rows)})", len(rows) == 2)
    by_name = {str(row[0]): row for row in rows}
    check("both expected Dataset names exist", set(by_name) == set(expected))
    for name, data in expected.items():
        row = by_name.get(name)
        if row is None:
            continue
        check(
            f"{name}: verified sha256 is the SHA-256 of the bytes the UI picked",
            row[1] == hashlib.sha256(data).hexdigest(),
        )
        check(f"{name}: verified size is {FILE_BYTES}", row[2] == FILE_BYTES)
        check(
            f"{name}: in the right organization and project", row[3] == org1_id and row[4] == p1_id
        )
        check(f"{name}: its session is verified", row[5] == "verified")
        try:
            head = client.head_object(bucket, str(row[6]))
            size = int(head.get("content-length", "-1"))
            check(f"{name}: the stored object has the picked size ({size})", size == FILE_BYTES)
        except S3Error as exc:
            check(f"{name}: the stored object exists ({exc})", False)
    check(f"no upload session in the other organization (got {org2_sessions})", org2_sessions == 0)
    check(f"no Dataset in the other organization (got {org2_datasets})", org2_datasets == 0)
    check(f"exactly two upload sessions exist (got {all_sessions})", all_sessions == 2)

    app_engine.dispose()
    owner_engine.dispose()
    if booted_here:
        s3lab.compose("stop", "s3", check_exit=False)
    _finish()


def _finish() -> None:
    if FAILURES:
        print(f"FTF-0501-UI real API (PG16 + SeaweedFS): {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0501-UI real API (PG16 + SeaweedFS): all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
