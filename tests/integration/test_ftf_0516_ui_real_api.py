#!/usr/bin/env python3
"""FTF-0516 UI -- the real issue-resolution component against the REAL FTF-0516-P1 router.

`apps/web/tests/ftf_0516.spec.ts` drives the component against a mocked `fetch`. This suite closes
the UI CHECKLIST's item (f): it boots `build_dataset_quality_router` on a real uvicorn server over
PostgreSQL 16 as the restricted `sovra_app` role, seeds real datasets / quality reports / issues
through the real stores, then runs `apps/web/tests/real_api/ftf_0516_ui_real_api.realapi.ts`
(Vitest + jsdom, the real component and real `fetch`) against it:

R1  exclude recorded, with the server's before/after evidence shown
R2  correct with an UPPERCASE-typed successor: sent lowercase, recorded with the successor
R3  waive: 403 for ml_engineer (nothing recorded), recorded for workspace_admin
R4  stale view: another decision lands first -> 409 shown as an error, nothing recorded
R5  lost response + another decision + identical retry: recorded, ONE row for the issue
R6  detector failure: no decision control, no POST
R7  foreign dataset (another organization's): 404 shown, nothing readable, nothing written

Afterwards this file checks the database independently of the browser side: exactly the rows the
scenarios must have produced (and none for R3's refused waive, R4's stale submit, R6, R7), the
stored evidence hashes, the actor identity and role taken from the authenticated principal, and
that no row exists in the other organization.

Requires SOVRA_DATABASE_URL (migration owner), SOVRA_APP_DB_PASSWORD, and Node with
`pnpm install` done.
Run: python3 tests/integration/test_ftf_0516_ui_real_api.py
"""

from __future__ import annotations

import importlib.util
import json
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
from app.datasets.dataset_quality_routes import build_dataset_quality_router  # noqa: E402
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402

CID = "FTF-0516-UI-F"
FAILURES: list[str] = []
HERE = Path(__file__).resolve().parent


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name[:-3], HERE / name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


p1 = _load("test_ftf_0516_p1_quality_issues.py")
qstore = p1.qstore
ustore = p1.ustore


def check(description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{CID}] {description}")


def main() -> None:
    migration_url = p1._required_env("SOVRA_DATABASE_URL")
    app_password = p1._required_env("SOVRA_APP_DB_PASSWORD")
    result = p1._alembic(["upgrade", "head"], migration_url)
    check(f"alembic upgrade head ({result.stdout}{result.stderr})", result.returncode == 0)
    if result.returncode != 0:
        return _finish()

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(p1._app_role_url(migration_url, app_password))
    check("the app engine is the restricted sovra_app role", app_engine.url.username == "sovra_app")
    factory = sessionmaker(bind=app_engine, expire_on_commit=False)

    with Session(owner_engine) as session:
        org1 = Organization(name="Acme UI16", slug=f"acme-ui16-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex UI16", slug=f"globex-ui16-{uuid.uuid4().hex[:8]}")
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

    def seed_connection(org: uuid.UUID, project_id: uuid.UUID) -> uuid.UUID:
        connection_id = uuid.uuid4()
        with owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO dataset_source_connections (id, organization_id, project_id, "
                    "source_type, display_name, allowed_purposes_json, security_classification, "
                    "connection_status, created_by) VALUES (:id, :org, :project, 'upload', "
                    "'upload-src', CAST(:purposes AS jsonb), 'internal', 'active', 'seed')"
                ),
                {
                    "id": connection_id,
                    "org": org,
                    "project": project_id,
                    "purposes": json.dumps(["training"]),
                },
            )
        return connection_id

    conn1, conn2 = seed_connection(org1_id, p1_id), seed_connection(org2_id, p2_id)
    ctx1, ctx2 = TenantContext(organization_id=org1_id), TenantContext(organization_id=org2_id)
    actor_seed = qstore.QualityActor("user", "seed-detector", "ml_engineer", "req-seed")
    upload_actor = ustore.UploadActor("user", "seed-user", "req-upload")

    def run_store(ctx: TenantContext, fn: Any) -> Any:
        with tenant_scoped_session_for(factory, ctx) as session:
            return fn(session)

    def make_dataset(
        name: str, ctx: TenantContext, project_id: uuid.UUID, connection: uuid.UUID
    ) -> Any:
        sha = p1._sha(f"{name}-{uuid.uuid4()}")

        def build(session: Session) -> Any:
            opened = ustore.create_upload_session(
                session,
                ctx,
                project_id=project_id,
                connection_id=connection,
                purpose="training",
                declared_size_bytes=100,
                declared_sha256=sha,
                declared_content_type="application/jsonl",
                max_bytes=100,
                actor=upload_actor,
            )
            return ustore.record_upload_verified_and_create_dataset(
                session,
                ctx,
                project_id=project_id,
                upload_session_id=opened.id,
                dataset_name=name,
                verified_size_bytes=100,
                verified_sha256=sha,
                verified_content_type="application/jsonl",
                actor=upload_actor,
            )

        return run_store(ctx, build)

    def record(
        dataset: Any, ctx: TenantContext, runs: Any = None, extra: tuple[Any, ...] = ()
    ) -> Any:
        return run_store(
            ctx,
            lambda s: qstore.record_quality_report(
                s,
                ctx,
                project_id=dataset.project_id,
                dataset_id=dataset.id,
                runs=runs if runs is not None else p1._full_runs(),
                extra_issues=extra,
                detector_versions=p1.VERSIONS,
                actor=actor_seed,
            ),
        )

    def issues_of(report: Any, ctx: TenantContext) -> dict[str, dict[str, Any]]:
        rows = run_store(
            ctx,
            lambda s: list(s.query(qstore.DatasetQualityIssue).filter_by(report_id=report.id)),
        )
        return {
            row.reason_code: {
                "id": row.issue_id,
                "evidence": row.evidence_sha256,
                "positions": list(row.batch_positions),
                "failure": bool(row.is_detector_failure),
                "severity": row.severity,
            }
            for row in rows
        }

    def scenario(
        dataset: Any, report: Any, ctx: TenantContext, project_id: uuid.UUID, org: uuid.UUID
    ) -> dict[str, Any]:
        return {
            "org": str(org),
            "project": str(project_id),
            "dataset": str(dataset.id),
            "report": str(report.id),
            "issues": issues_of(report, ctx),
        }

    warn = p1._extra
    seeds: dict[str, dict[str, Any]] = {}

    d = make_dataset("ui-exclude", ctx1, p1_id, conn1)
    seeds["r1"] = scenario(d, record(d, ctx1), ctx1, p1_id, org1_id)

    d_v1 = make_dataset("ui-correct", ctx1, p1_id, conn1)
    r_v1 = record(d_v1, ctx1, extra=(warn("ui_c_warn", (0, 1)),))
    d_v2 = make_dataset("ui-correct", ctx1, p1_id, conn1)  # the later version = the successor
    record(d_v2, ctx1, p1._full_runs(clean=True))
    seeds["r2"] = scenario(d_v1, r_v1, ctx1, p1_id, org1_id)
    seeds["r2"]["successor"] = str(d_v2.id)

    d = make_dataset("ui-waive", ctx1, p1_id, conn1)
    seeds["r3"] = scenario(
        d, record(d, ctx1, extra=(warn("ui_w_warn", (0, 1)),)), ctx1, p1_id, org1_id
    )

    d = make_dataset("ui-stale", ctx1, p1_id, conn1)
    extras = (warn("ui_s_warn_a", (0, 1)), warn("ui_s_warn_b", (0, 1)))
    seeds["r4"] = scenario(d, record(d, ctx1, extra=extras), ctx1, p1_id, org1_id)

    d = make_dataset("ui-lost", ctx1, p1_id, conn1)
    extras = (warn("ui_l_warn_a", (0, 1)), warn("ui_l_warn_b", (0, 1)))
    seeds["r5"] = scenario(d, record(d, ctx1, extra=extras), ctx1, p1_id, org1_id)

    d = make_dataset("ui-failure", ctx1, p1_id, conn1)
    runs = p1._full_runs(clean=True, drop=("near_duplicate",))
    seeds["r6"] = scenario(d, record(d, ctx1, runs), ctx1, p1_id, org1_id)

    d = make_dataset("ui-foreign", ctx2, p2_id, conn2)
    seeds["r7"] = scenario(d, record(d, ctx2), ctx2, p2_id, org2_id)
    # the UI is pointed at ORG 1's path with ORG 2's dataset id
    seeds["r7"]["org"] = str(org1_id)
    seeds["r7"]["project"] = str(p1_id)

    fixture = p1._Fixture({"org1": org1_id, "org2": org2_id})
    app = FastAPI()
    app.include_router(
        build_dataset_quality_router(
            factory,
            principal_resolver=fixture.principal_resolver,
            role_lookup=fixture.role_lookup,
        )
    )

    with p1._running_app(app) as base:
        env = dict(os.environ)
        env.update(
            {
                "FTF_0516_UI_API_BASE": base,
                "FTF_0516_UI_SEEDS": json.dumps(seeds),
                "FTF_0516_UI_PRINCIPAL_ML": fixture.header_for("org1", Role.ML_ENGINEER),
                "FTF_0516_UI_PRINCIPAL_ADMIN": fixture.header_for("org1", Role.WORKSPACE_ADMIN),
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
                "ftf_0516_ui_real_api",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
        )
        print(ran.stdout[-8000:])
        print(ran.stderr[-3000:])
        check(f"the Vitest real-API run exits 0 (got {ran.returncode})", ran.returncode == 0)
        plain = re.sub(r"\x1b\[[0-9;]*m", "", ran.stdout)
        for name in ("R1", "R2", "R3", "R4", "R5", "R6", "R7"):
            check(
                f"the Vitest run executed and passed {name}",
                re.search(rf"✓.*\b{name} real ", plain) is not None,
            )

    # ---- independent database verification ----------------------------------------------
    def rows_for(report_id: str) -> list[Any]:
        with owner_engine.connect() as conn:
            return list(
                conn.execute(
                    text(
                        "SELECT issue_id, action, engine_decision, excluded_batch_positions, "
                        "successor_dataset_id, before_evidence_sha256, after_evidence_sha256, "
                        "actor_id, actor_role, resolution_version_after, organization_id, reason "
                        "FROM dataset_issue_resolutions WHERE quality_report_id = :r "
                        "ORDER BY resolution_version_after"
                    ),
                    {"r": report_id},
                )
            )

    def resolution_version(report_id: str) -> int:
        with owner_engine.connect() as conn:
            return int(
                conn.execute(
                    text("SELECT resolution_version FROM dataset_quality_reports WHERE id = :r"),
                    {"r": report_id},
                ).scalar()
                or 0
            )

    s = seeds
    # R1: one exclude of the pii issue, position 2, server-computed before AND after evidence
    pii = s["r1"]["issues"]["pii_secret_email"]
    rows = rows_for(s["r1"]["report"])
    check(f"R1: exactly one decision row (got {len(rows)})", len(rows) == 1)
    if rows:
        row = rows[0]
        check("R1: it is the exclude of the pii issue", row[0] == pii["id"] and row[1] == "exclude")
        check("R1: it excluded position 2 only", list(row[3]) == [2])
        check("R1: before evidence is the issue's stored evidence", row[5] == pii["evidence"])
        check("R1: after evidence is server-computed and different", row[6] not in (None, row[5]))
        check(
            "R1: actor identity and role come from the principal",
            row[7] == "user-org1-ml_engineer" and row[8] == "ml_engineer",
        )
        check(
            "R1: resolution version is 1",
            row[9] == 1 and resolution_version(s["r1"]["report"]) == 1,
        )

    # R2: the correct decision binds the lowercase successor
    rows = rows_for(s["r2"]["report"])
    check(f"R2: exactly one decision row (got {len(rows)})", len(rows) == 1)
    if rows:
        check(
            "R2: action correct, successor bound lowercase",
            rows[0][1] == "correct" and str(rows[0][4]) == s["r2"]["successor"],
        )

    # R3: ml_engineer's waive wrote nothing; the admin's is the only row
    rows = rows_for(s["r3"]["report"])
    check(f"R3: exactly one decision row (got {len(rows)})", len(rows) == 1)
    if rows:
        check(
            "R3: the only row is the workspace_admin's waiver",
            rows[0][1] == "waive"
            and rows[0][2] == "waived"
            and rows[0][8] == "workspace_admin"
            and rows[0][7] == "user-org1-workspace_admin",
        )

    # R4: only the competing decision exists; the stale UI submit recorded nothing
    rows = rows_for(s["r4"]["report"])
    check(f"R4: exactly the competing decision exists (got {len(rows)})", len(rows) == 1)
    if rows:
        check(
            "R4: it is the competing decision on warn_b, not the stale UI's warn_a",
            rows[0][0] == s["r4"]["issues"]["ui_s_warn_b"]["id"],
        )

    # R5: one row per decided issue; the retried issue has exactly ONE row
    rows = rows_for(s["r5"]["report"])
    mine = [r for r in rows if r[0] == s["r5"]["issues"]["ui_l_warn_a"]["id"]]
    others = [r for r in rows if r[0] == s["r5"]["issues"]["ui_l_warn_b"]["id"]]
    check(f"R5: the retried issue has exactly one row (got {len(mine)})", len(mine) == 1)
    check(f"R5: the competing issue has exactly one row (got {len(others)})", len(others) == 1)
    check(f"R5: two rows in total (got {len(rows)})", len(rows) == 2)
    check("R5: report version is 2", resolution_version(s["r5"]["report"]) == 2)

    # R6 / R7: nothing written
    check("R6: a detector failure recorded no decision", rows_for(s["r6"]["report"]) == [])
    check("R7: the foreign dataset's report has no decision", rows_for(s["r7"]["report"]) == [])
    with owner_engine.connect() as conn:
        org2_rows = conn.execute(
            text("SELECT count(*) FROM dataset_issue_resolutions WHERE organization_id = :o"),
            {"o": org2_id},
        ).scalar()
    check(f"no decision row in the other organization (got {org2_rows})", org2_rows == 0)

    app_engine.dispose()
    owner_engine.dispose()
    _finish()


def _finish() -> None:
    if FAILURES:
        print(f"FTF-0516 UI real API (PG16): {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0516 UI real API (PG16): all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
