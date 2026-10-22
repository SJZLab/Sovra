#!/usr/bin/env python3
"""FTF-UX04 -- the real project form and requirements panel against the REAL routers.

`apps/web/tests/ftf_ux04.spec.ts` drives the two components against a mocked `fetch`. This suite
closes the UI CHECKLIST's item (f): it boots `build_project_router`,
`build_budget_policy_router` and
`build_critical_behavior_requirement_router` on a real uvicorn server over PostgreSQL 16 as the
restricted `sovra_app` role, seeds real organizations / workspaces / projects / tools through the
real stores, then runs `apps/web/tests/real_api/ftf_ux04_real_api.realapi.ts` (Vitest + jsdom, the
real components and real `fetch`) against it:

R1   create a generic project                      -> 201, bound, "Project created"
R2   create an agentic project, then save again    -> POST then PATCH (no duplicate), profile kept
R3   edit, then clear the description              -> "" stored, version +1 each save
R4   stale edit (another editor saved first)       -> 409 shown as a sentence, nothing overwritten
R5   save a draft, reopen                          -> draft stored, version unchanged, offered back
R6   read-only role                                -> 403 shown, nothing changed
R7   another organization's project, this path     -> 404 shown, nothing written
R8   profile less strict than the org default      -> 400 with the reason, no project; then accepted
R9   the real /requirements view                   -> rows and states in words
R10  create then revise a budget policy            -> bound replies, versions 1 then 2, exact
R11  total below the reserved amount               -> stopped before a request; stale revise 409
R12  read-only role on the budget                  -> 403 shown, no policy
R13  another organization's project (budget)       -> 404 shown, nothing stored

Afterwards this file checks the database independently of the browser side: exactly the rows the
scenarios must have produced (and none for R6/R7/R8's refused attempts), the actor taken from the
authenticated principal, the stored description/ui_state/versions, the exact stored amounts, and
that nothing was written into the other organization.

Requires SOVRA_DATABASE_URL (migration owner), SOVRA_APP_DB_PASSWORD, and Node with
`pnpm install` done.
Run: python3 tests/integration/test_ftf_ux04_real_api.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from fastapi import FastAPI  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.auth.organization_workspace_schema import Organization, Workspace  # noqa: E402
from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.budget.budget_policy_routes import build_budget_policy_router  # noqa: E402
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402
from app.projects.project_crud import ActorInfo as ProjectActor  # noqa: E402
from app.projects.project_crud import create_project  # noqa: E402
from app.projects.project_routes import build_project_router  # noqa: E402
from app.tools.critical_behavior_requirement_routes import (  # noqa: E402
    build_critical_behavior_requirement_router,
)
from app.tools.tool_definition_crud import ActorInfo as ToolActor  # noqa: E402
from app.tools.tool_definition_crud import create_tool_definition  # noqa: E402

CID = "FTF-UX04-F"
FAILURES: list[str] = []
HERE = Path(__file__).resolve().parent
ISSUER = "https://issuer.example"


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name[:-3], HERE / name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# Reuse FTF-0401's own harness helpers (env contract, migration, restricted-role URL, real server).
base = _load("test_ftf_0401_project_schema_api.py")


def check(description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{CID}] {description}")


class Fixture:
    """Principals by header; membership per organization."""

    def __init__(self, org_ids: dict[str, uuid.UUID]) -> None:
        self.principals: dict[tuple[str, Role], AuthenticatedPrincipal] = {}
        self.memberships: dict[tuple[str, str, uuid.UUID], Role] = {}
        for label, org_id in org_ids.items():
            for role in (Role.WORKSPACE_ADMIN, Role.EVALUATOR):
                subject = f"user-{label}-{role.value}"
                self.principals[(label, role)] = AuthenticatedPrincipal(
                    issuer=ISSUER, subject=subject
                )
                self.memberships[(ISSUER, subject, org_id)] = role

    @staticmethod
    def header_for(label: str, role: Role) -> str:
        return f"{label}:{role.value}"

    def principal_resolver(self, request: Any) -> AuthenticatedPrincipal | None:
        header = request.headers.get("x-test-principal")
        if header is None:
            return None
        label, _, role_value = header.partition(":")
        try:
            role = Role(role_value)
        except ValueError:
            return None
        return self.principals.get((label, role))

    def role_lookup(self, issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
        return self.memberships.get((issuer, subject, organization_id))


def _finish() -> None:
    if FAILURES:
        print(f"{CID}: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print(f"{CID}: all real-API checks passed")


def main() -> None:
    migration_url = base._required_env("SOVRA_DATABASE_URL")
    app_password = base._required_env("SOVRA_APP_DB_PASSWORD")
    migrated = base._run_migration(migration_url)
    check(f"alembic upgrade head ({migrated.stdout}{migrated.stderr})", migrated.returncode == 0)
    if migrated.returncode != 0:
        return _finish()

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(base._app_role_url(migration_url, app_password))
    check("the app engine is the restricted sovra_app role", app_engine.url.username == "sovra_app")
    factory = sessionmaker(bind=app_engine, expire_on_commit=False)

    with Session(owner_engine) as session:
        org1 = Organization(name="Acme UX04", slug=f"acme-ux04-{uuid.uuid4().hex[:8]}")
        org2 = Organization(name="Globex UX04", slug=f"globex-ux04-{uuid.uuid4().hex[:8]}")
        session.add_all([org1, org2])
        session.flush()
        ws1 = Workspace(organization_id=org1.id, name="Main", slug="main")
        ws2 = Workspace(organization_id=org2.id, name="Main", slug="main")
        session.add_all([ws1, ws2])
        session.commit()
        org1_id, org2_id, ws1_id, ws2_id = org1.id, org2.id, ws1.id, ws2.id
    with owner_engine.begin() as conn:
        # org2 inherits a stricter default: a project there may not be less strict (R8)
        conn.execute(
            text(
                "UPDATE organizations SET default_security_profile = 'zero_egress' WHERE id = :id"
            ),
            {"id": org2_id},
        )

    ctx1, ctx2 = TenantContext(organization_id=org1_id), TenantContext(organization_id=org2_id)
    seed_actor = ProjectActor(actor_type="user", actor_id="seed", request_id="req-seed")

    def seed_project(ctx: TenantContext, ws: uuid.UUID, name: str, profile: str) -> uuid.UUID:
        with tenant_scoped_session_for(factory, ctx) as session:
            created = create_project(
                session,
                ctx,
                workspace_id=ws,
                name=name,
                project_type="generic_model_tuning",
                description=None,
                agent_objective=None,
                security_profile=profile,
                actor=seed_actor,
            )
            return created.id

    seeds_ids = {
        "edit": seed_project(ctx1, ws1_id, "ux04-seed-edit", "standard"),
        "stale": seed_project(ctx1, ws1_id, "ux04-seed-stale", "standard"),
        "draft": seed_project(ctx1, ws1_id, "ux04-seed-draft", "standard"),
        "role": seed_project(ctx1, ws1_id, "ux04-seed-role", "standard"),
        "budget": seed_project(ctx1, ws1_id, "ux04-seed-budget", "standard"),
        "budgetStale": seed_project(ctx1, ws1_id, "ux04-seed-budget-stale", "standard"),
        "budgetRole": seed_project(ctx1, ws1_id, "ux04-seed-budget-role", "standard"),
        "requirements": seed_project(ctx1, ws1_id, "ux04-seed-requirements", "standard"),
        "foreign": seed_project(ctx2, ws2_id, "ux04-seed-foreign", "zero_egress"),
    }
    tool_actor = ToolActor(actor_type="user", actor_id="seed", request_id="req-tools")
    schema: dict[str, object] = {"type": "object", "properties": {"order_id": {"type": "string"}}}
    with tenant_scoped_session_for(factory, ctx1) as session:
        for name, effect, critical, confirm in (
            ("issue_refund", "financial", True, True),
            ("lookup_order", "read_only", False, False),
        ):
            create_tool_definition(
                session,
                ctx1,
                project_id=seeds_ids["requirements"],
                name=name,
                description=None,
                input_schema_json=schema,
                output_schema_json=schema,
                side_effect_class=effect,
                requires_confirmation=confirm,
                is_critical=critical,
                actor=tool_actor,
            )

    fixture = Fixture({"org1": org1_id, "org2": org2_id})
    app = FastAPI()
    for router in (
        build_project_router(
            factory, principal_resolver=fixture.principal_resolver, role_lookup=fixture.role_lookup
        ),
        build_budget_policy_router(
            factory, principal_resolver=fixture.principal_resolver, role_lookup=fixture.role_lookup
        ),
        build_critical_behavior_requirement_router(
            factory, principal_resolver=fixture.principal_resolver, role_lookup=fixture.role_lookup
        ),
    ):
        app.include_router(router)

    seeds = {
        "org1": str(org1_id),
        "org2": str(org2_id),
        "ws1": str(ws1_id),
        "ws2": str(ws2_id),
        **{key: str(value) for key, value in seeds_ids.items()},
    }
    with base._running_app(app) as api_base:
        env = dict(os.environ)
        env.update(
            {
                "UX04_API_BASE": api_base,
                "UX04_SEEDS": json.dumps(seeds),
                "UX04_PRINCIPAL_ADMIN": fixture.header_for("org1", Role.WORKSPACE_ADMIN),
                "UX04_PRINCIPAL_EVALUATOR": fixture.header_for("org1", Role.EVALUATOR),
                "UX04_PRINCIPAL_ORG2_ADMIN": fixture.header_for("org2", Role.WORKSPACE_ADMIN),
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
                "ftf_ux04_real_api",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
        )
    print(ran.stdout[-9000:])
    print(ran.stderr[-3000:])
    check(f"the Vitest real-API run exits 0 (got {ran.returncode})", ran.returncode == 0)
    plain = re.sub(r"\x1b\[[0-9;]*m", "", ran.stdout)
    for name in tuple(f"R{n}" for n in range(1, 14)):
        check(f"scenario {name} ran and passed", re.search(rf"✓.*\b{name}\b", plain) is not None)

    # ---- the database, checked independently of the browser side ---------------------------
    def one(sql: str, **params: Any) -> Any:
        with owner_engine.connect() as conn:
            return conn.execute(text(sql), params).mappings().first()

    def count(sql: str, **params: Any) -> int:
        with owner_engine.connect() as conn:
            return int(conn.execute(text(sql), params).scalar_one())

    r1 = one("SELECT * FROM projects WHERE name = 'ux04-r1-generic'")
    check("R1 exactly the created project exists", r1 is not None)
    if r1 is not None:
        check(
            "R1 stored as sent", r1["description"] == "made by the UI" and r1["status"] == "active"
        )
        check(
            "R1 actor is the authenticated principal",
            r1["created_by"] == "user-org1-workspace_admin",
        )
        check("R1 version 1, org1", r1["row_version"] == 1 and r1["organization_id"] == org1_id)

    check(
        "R2 one project for the agentic create (the second save renamed it; no duplicate)",
        count("SELECT count(*) FROM projects WHERE name LIKE 'ux04-r2-%'") == 1,
    )
    r2 = one("SELECT * FROM projects WHERE name = 'ux04-r2-agentic-renamed'")
    check("R2 the rename landed on the same row", r2 is not None)
    if r2 is not None:
        check(
            "R2 agentic, setup incomplete, zero_egress, version 2",
            r2["project_type"] == "agentic_product"
            and r2["status"] == "setup_incomplete"
            and r2["security_profile"] == "zero_egress"
            and r2["row_version"] == 2
            and r2["agent_objective"] == "Triage support tickets.",
        )

    r3 = one("SELECT * FROM projects WHERE id = :id", id=seeds_ids["edit"])
    check(
        "R3 description cleared to an empty string, two saves (version 3)",
        r3 is not None and r3["description"] == "" and r3["row_version"] == 3,
    )
    r4 = one("SELECT * FROM projects WHERE id = :id", id=seeds_ids["stale"])
    check(
        "R4 the other editor's save stands, the UI's stale save did not overwrite it",
        r4 is not None and r4["name"] == "ux04-r4-by-other-editor" and r4["row_version"] == 2,
    )
    r5 = one("SELECT * FROM projects WHERE id = :id", id=seeds_ids["draft"])
    check(
        "R5 the draft is stored in ui_state, the project itself and its version are untouched",
        r5 is not None
        and r5["row_version"] == 1
        and r5["name"] == "ux04-seed-draft"
        and r5["ui_state_json"].get("name") == "ux04-r5-work-in-progress"
        and r5["ui_state_json"].get("description") == "half-written notes",
    )
    r6 = one("SELECT * FROM projects WHERE id = :id", id=seeds_ids["role"])
    check(
        "R6 the read-only role changed nothing",
        r6 is not None and r6["name"] == "ux04-seed-role" and r6["row_version"] == 1,
    )
    r7 = one("SELECT * FROM projects WHERE id = :id", id=seeds_ids["foreign"])
    check(
        "R7 nothing was written into the other organization's project",
        r7 is not None and r7["name"] == "ux04-seed-foreign" and r7["row_version"] == 1,
    )
    check(
        "R7 no project carries the cross-tenant attempt's name",
        count("SELECT count(*) FROM projects WHERE name = 'ux04-r7-cross-tenant'") == 0,
    )
    check(
        "R8 the refused relaxed profile created nothing, the accepted one created exactly one",
        count("SELECT count(*) FROM projects WHERE name = 'ux04-r8-relaxed'") == 1
        and count(
            "SELECT count(*) FROM projects WHERE name = 'ux04-r8-relaxed' "
            "AND security_profile = 'zero_egress' AND organization_id = :org",
            org=org2_id,
        )
        == 1,
    )

    b10 = one("SELECT * FROM budget_policies WHERE project_id = :id", id=seeds_ids["budget"])
    check("R10 one budget policy exists for the project", b10 is not None)
    if b10 is not None:
        check(
            "R10 exact stored amount, currency, scopes and version",
            b10["total_amount"] == Decimal("3000.0000")
            and b10["currency"] == "SAR"
            and sorted(b10["scopes_json"]) == ["evaluation", "serving", "training"]
            and b10["row_version"] == 2
            and b10["organization_id"] == org1_id,
        )
    b11 = one("SELECT * FROM budget_policies WHERE project_id = :id", id=seeds_ids["budgetStale"])
    check(
        "R11 the other editor's total stands; the below-reserved and stale attempts wrote nothing",
        b11 is not None
        and b11["total_amount"] == Decimal("1200.0000")
        and b11["reserved_amount"] == Decimal("400.0000"),
    )
    check(
        "R12 the read-only role stored no policy",
        count(
            "SELECT count(*) FROM budget_policies WHERE project_id = :id",
            id=seeds_ids["budgetRole"],
        )
        == 0,
    )
    check(
        "R13 nothing was stored for the other organization's project",
        count(
            "SELECT count(*) FROM budget_policies WHERE project_id = :id", id=seeds_ids["foreign"]
        )
        == 0,
    )
    check(
        "no row in org2 carries an org1 actor",
        count(
            "SELECT count(*) FROM projects "
            "WHERE organization_id = :org AND created_by LIKE 'user-org1-%'",
            org=org2_id,
        )
        == 0,
    )
    _finish()


if __name__ == "__main__":
    main()
