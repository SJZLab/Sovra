#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg,index,union-attr,misc"
# ruff: noqa: E501
"""FTF-0617 -- real Postgres proof of the zero-egress judge policy's database half.

`tests/tasks/test_ftf_0617.py` proves the policy logic on the real FTF-0901 / H005 / 0616 code with the
database boundary faked. This test runs the same module with NOTHING faked at that boundary: a real
`sovra_app` session through the real `tenant_scoped_session`, the real `execute_tenant_scoped` SQL,
the real FTF-0201 `get_connection` and the real FTF-0103A `lookup_org_role` over real RLS, plus the
FTF-0616 local TLS gateway for the admission attestation and the judge call.

* profile in force: the complete org x project x job truth table (3 x 3 x 4, including no job
  profile) equals an independently written most-restrictive oracle, including a project row weakened
  by raw SQL under a `zero_egress` organization;
* a foreign project, a missing project and a project asked for under another organization's context
  are one identical `judge_policy_not_found`;
* a revoked connection is refused; a foreign connection is identical to a missing one;
* approver and reviewer role tables over all six roles, plus a revoked membership, a membership of
  another organization and a workspace-scoped membership (none authorizes);
* reviewer independence (exact and NFKC+casefold+strip) over real stored identities, and exact
  lookups (a case-variant is a different, unauthorized, subject);
* an end-to-end private plan and a verdict through the real session;
* the policy writes nothing: `audit_events` and every row count it can reach are unchanged.

Requires a live Postgres at `SOVRA_DATABASE_URL` (migration owner, superuser in CI) plus
`SOVRA_APP_DB_PASSWORD`, like every prior integration test. Run directly:
    SOVRA_DATABASE_URL=postgresql+psycopg://<owner>@host/db \\
    SOVRA_APP_DB_PASSWORD=<app-role-password> \\
    python3 tests/integration/test_ftf_0617_zero_egress_judge.py
"""

from __future__ import annotations

import itertools
import os
import subprocess
import sys
import unicodedata
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(ROOT / "tests" / "tasks"))

import test_ftf_0617 as u  # noqa: E402  (the unit suite's gateway + approval helpers; its DB fakes are NOT installed here)
import workers.evaluation.zero_egress_judge_policy as m  # noqa: E402
from packages.security.security_profile_enforcement import Destination  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.auth.organization_workspace_schema import (  # noqa: E402
    APP_ROLE_NAME,
    Organization,
    Workspace,
)
from app.auth.rbac_middleware import Role  # noqa: E402
from app.auth.tenant_isolation_query_guards import tenant_scoped_session  # noqa: E402
from app.db.tenant_context import TenantContext  # noqa: E402
from app.litellm.connection_schema import LiteLLMConnection  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402

FAILURES: list[str] = []
PROFILES = ("standard", "zero_egress", "air_gapped")
RANK = {"standard": 0, "zero_egress": 1, "air_gapped": 2}
IDP = u.IDP


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"BLOCKED_EXTERNAL: {name} is not set -- the zero-egress judge evidence requires a "
            "live Postgres (migration owner connection). Not silently skipped.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def _app_role_url(migration_url: str, app_password: str) -> str:
    parts = urlsplit(migration_url)
    netloc = f"{APP_ROLE_NAME}:{app_password}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def oracle(org: str, project: str, job: str | None) -> str:
    """Independently written: the most restrictive of the three."""
    return max((org, project, job or "standard"), key=lambda name: RANK[name])


def main() -> None:
    if u.h._TRUSTED_LEAF is None:
        print(
            "BLOCKED_EXTERNAL: no openssl/update-ca-certificates/sudo -- the real local TLS "
            "gateway cannot start; the gateway evidence is not skipped.",
            file=sys.stderr,
        )
        sys.exit(1)
    migration_url = _required_env("SOVRA_DATABASE_URL")
    app_password = _required_env("SOVRA_APP_DB_PASSWORD")

    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
        env=dict(os.environ),
    )
    check(
        "FTF-0617-I0",
        f"alembic upgrade head (exit {upgrade.returncode})\n{upgrade.stderr}",
        upgrade.returncode == 0,
    )
    if FAILURES:
        _finish()

    owner_engine = create_engine(migration_url)
    app_engine = create_engine(_app_role_url(migration_url, app_password))

    def factory() -> Session:
        return Session(app_engine)

    suffix = uuid.uuid4().hex[:8]
    base_url = f"https://127.0.0.1:{u.PORT}"
    orgs: dict[str, uuid.UUID] = {}  # org profile -> org id
    projects: dict[tuple[str, str], uuid.UUID] = {}  # (org profile, project profile) -> project id
    connections: dict[str, uuid.UUID] = {}
    with Session(owner_engine) as owner:
        for org_profile in PROFILES:
            org = Organization(
                name=f"Org {org_profile}", slug=f"z0617-{org_profile.replace('_', '')}-{suffix}"
            )
            owner.add(org)
            owner.flush()
            owner.execute(
                text("UPDATE organizations SET default_security_profile = :p WHERE id = :id"),
                {"p": org_profile, "id": org.id},
            )
            workspace = Workspace(organization_id=org.id, name="Main", slug="main")
            owner.add(workspace)
            owner.flush()
            for project_profile in PROFILES:
                project = Project(
                    organization_id=org.id,
                    workspace_id=workspace.id,
                    name=f"p-{project_profile}",
                    project_type="generic_model_tuning",
                    status="active",
                    security_profile="standard",
                    row_version=1,
                    created_by="seed",
                )
                owner.add(project)
                owner.flush()
                # The weakening / strengthening is applied by raw SQL, the way an admin or a bug would.
                owner.execute(
                    text("UPDATE projects SET security_profile = :p WHERE id = :id"),
                    {"p": project_profile, "id": project.id},
                )
                projects[(org_profile, project_profile)] = project.id
            connection = LiteLLMConnection(
                organization_id=org.id,
                name="gateway",
                base_url=base_url,
                secret_ref=u.h._SECRET_REF,
                status="active",
            )
            owner.add(connection)
            owner.flush()
            connections[org_profile] = connection.id
            orgs[org_profile] = org.id
        owner.commit()

    org_ze, org_std = orgs["zero_egress"], orgs["standard"]
    project_ze = projects[("zero_egress", "standard")]  # a weakened project under a zero_egress org
    conn_ze = connections["zero_egress"]

    # -- Memberships: every role, plus the ones that must not authorize ------------------------
    role_subject = {role: (IDP, f"{role.value}-1") for role in Role}
    with Session(owner_engine) as owner:
        for role, (issuer, subject) in role_subject.items():
            owner.execute(
                text(
                    "INSERT INTO memberships (organization_id, issuer, subject, role, status) "
                    "VALUES (:o, :i, :s, :r, 'active')"
                ),
                {"o": org_ze, "i": issuer, "s": subject, "r": role.value},
            )
        extra = (
            (org_ze, IDP, "admin-1", "organization_admin", "active"),
            (org_ze, IDP, "revoked-admin", "organization_admin", "revoked"),
            (org_std, IDP, "only-in-other-org", "organization_admin", "active"),
            (org_ze, IDP, "producer-1", "evaluator", "active"),
            (org_ze, IDP, "PRODUCER-1", "evaluator", "active"),
            (org_ze, IDP, "Producer-1", "evaluator", "active"),
            (org_ze, IDP, "producer-1 ", "evaluator", "active"),
            (org_ze, IDP, "ｐroducer-1", "evaluator", "active"),
            (org_ze, IDP, "someone-else", "evaluator", "active"),
            (org_ze, "https://other-idp.test", "producer-1", "evaluator", "active"),
        )
        for org_id, issuer, subject, role, status in extra:
            owner.execute(
                text(
                    "INSERT INTO memberships (organization_id, issuer, subject, role, status) "
                    "VALUES (:o, :i, :s, :r, :st)"
                ),
                {"o": org_id, "i": issuer, "s": subject, "r": role, "st": status},
            )
        workspace_id = owner.execute(
            text("SELECT id FROM workspaces WHERE organization_id = :o"), {"o": org_ze}
        ).scalar_one()
        owner.execute(
            text(
                "INSERT INTO memberships (organization_id, workspace_id, issuer, subject, role, status) "
                "VALUES (:o, :w, :i, 'workspace-scoped-admin', 'organization_admin', 'active')"
            ),
            {"o": org_ze, "w": workspace_id, "i": IDP},
        )
        owner.commit()

    def counts() -> dict[str, int]:
        with Session(owner_engine) as owner:
            return {
                table: owner.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                for table in (
                    "audit_events",
                    "projects",
                    "litellm_connections",
                    "memberships",
                    "organizations",
                )
            }

    def decide(org_id: uuid.UUID, **overrides):
        kwargs = {
            "project_id": project_ze,
            "job_profile": None,
            "connection_id": conn_ze,
            "approval": u.approval_json(),
            "human_reviewers": (),
            "candidate_producer": u.PRODUCER,
            "judge_destination": None,
            "authorization": None,
            "secret_config": u.h._SECRET_CONFIG,
            "policy": u.h._policy_for(u.PORT),
            "now": u.NOW,
        }
        kwargs.update(overrides)
        with tenant_scoped_session(factory, org_id) as session:
            return m.decide_judge_plan(
                session=session,
                context=TenantContext(org_id),
                session_factory=factory,
                **kwargs,
            )

    def code(call) -> str:
        try:
            call()
        except m.ZeroEgressJudgePolicyError as exc:
            return exc.code
        except Exception as exc:  # noqa: BLE001
            return f"EXC:{type(exc).__name__}: {exc}"
        return ""

    before = counts()

    # -- I1: profile in force, the complete truth table --------------------------------------------
    for org_profile, project_profile, job in itertools.product(
        PROFILES, PROFILES, (None, *PROFILES)
    ):
        with tenant_scoped_session(factory, orgs[org_profile]) as session:
            got = m.load_profiles_in_force(
                session,
                TenantContext(orgs[org_profile]),
                projects[(org_profile, project_profile)],
                job,
            )
        want = oracle(org_profile, project_profile, job)
        check(
            "FTF-0617-I1",
            f"org={org_profile} project={project_profile} job={job}: effective {got[2]!r}, want {want!r}",
            got == (org_profile, project_profile, want),
        )
    # The weakened project row under the zero_egress org is still strict, end to end.
    u.reset()
    plan = decide(org_ze, approval=None)
    check(
        "FTF-0617-I1b",
        "a weakened project under a zero_egress org is still strict",
        plan.profile == "zero_egress" and plan.mode.value == "blocked",
    )
    plan = decide(org_ze, approval=None, job_profile="air_gapped")
    check(
        "FTF-0617-I1c", "a stricter job snapshot raises the profile", plan.profile == "air_gapped"
    )
    # The standard path: all three standard.
    plan = decide(
        org_std,
        project_id=projects[("standard", "standard")],
        connection_id=connections["standard"],
        approval=None,
        judge_destination=Destination("internal"),
    )
    check(
        "FTF-0617-I1d",
        "all-standard gives the standard judge for an internal destination",
        plan.mode.value == "standard_llm_judge",
    )

    # -- I2: not found is one answer ------------------------------------------------------------------
    def failure(org_id, **kw):
        try:
            decide(org_id, **kw)
        except m.ZeroEgressJudgePolicyError as exc:
            return (type(exc).__name__, exc.code, str(exc))
        return None

    missing = failure(org_ze, project_id=uuid.uuid4())
    foreign = failure(org_ze, project_id=projects[("standard", "standard")])
    other_ctx = failure(org_std, project_id=project_ze, connection_id=connections["standard"])
    check(
        "FTF-0617-I2",
        f"missing project: {missing}",
        missing is not None and missing[1] == "judge_policy_not_found",
    )
    check(
        "FTF-0617-I2b",
        f"foreign project differs from missing: {foreign} vs {missing}",
        foreign == missing,
    )
    check(
        "FTF-0617-I2c",
        f"project asked for under another organization: {other_ctx}",
        other_ctx == missing,
    )

    # -- I3: connections ------------------------------------------------------------------------------
    check(
        "FTF-0617-I3",
        "foreign connection differs from missing",
        code(lambda: decide(org_ze, connection_id=connections["standard"]))
        == code(lambda: decide(org_ze, connection_id=uuid.uuid4()))
        == "judge_policy_not_found",
    )
    with Session(owner_engine) as owner:
        owner.execute(
            text(
                "UPDATE litellm_connections SET status = 'revoked', revoked_at = now() WHERE id = :id"
            ),
            {"id": conn_ze},
        )
        owner.commit()
    check(
        "FTF-0617-I3b",
        "revoked connection refused",
        code(lambda: decide(org_ze)) == "judge_gateway_revoked",
    )
    check("FTF-0617-I3c", "no gateway request for a revoked connection", not u.G.requests)
    with Session(owner_engine) as owner:
        owner.execute(
            text(
                "UPDATE litellm_connections SET status = 'active', revoked_at = NULL WHERE id = :id"
            ),
            {"id": conn_ze},
        )
        owner.commit()

    # -- I4: approver role table ----------------------------------------------------------------------
    for role, identity in role_subject.items():
        u.reset()
        plan = decide(
            org_ze,
            approval=u.approval_json(
                approved_by_issuer=identity[0], approved_by_subject=identity[1]
            ),
        )
        want = role in (Role.ORGANIZATION_ADMIN, Role.WORKSPACE_ADMIN)
        check(
            "FTF-0617-I4",
            f"approver role {role.value}: private={plan.mode.value == 'private_llm_judge'}, want {want}",
            (plan.mode.value == "private_llm_judge") == want,
        )
        if not want:
            check(
                "FTF-0617-I4b",
                f"approver role {role.value}: defect {plan.approval_defect}",
                plan.approval_defect == "judge_approval_not_authorized",
            )
    for label, subject in (
        ("revoked membership", "revoked-admin"),
        ("membership of another organization", "only-in-other-org"),
        ("workspace-scoped membership", "workspace-scoped-admin"),
        ("no membership", "nobody"),
        ("case variant of an admin", "ORGANIZATION_ADMIN-1"),
    ):
        u.reset()
        plan = decide(org_ze, approval=u.approval_json(approved_by_subject=subject))
        check(
            "FTF-0617-I4c",
            f"approver {label} authorized the approval ({plan.mode.value}/{plan.approval_defect})",
            plan.mode.value != "private_llm_judge"
            and plan.approval_defect == "judge_approval_not_authorized",
        )
        check(
            "FTF-0617-I4d",
            f"approver {label}: candidate content reached the gateway",
            u.G.candidate_requests == 0,
        )

    # -- I5: reviewer role table + independence --------------------------------------------------------
    def authorize(reviewer, producer=u.PRODUCER, org_id=None):
        return m.authorize_human_judgment(
            session_factory=factory,
            organization_id=org_id or org_ze,
            reviewer=reviewer,
            candidate_producer=producer,
        )

    allowed = {Role.EVALUATOR, Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN}
    for role, identity in role_subject.items():
        got = code(lambda identity=identity: authorize(identity))
        want = "" if role in allowed else "human_reviewer_not_authorized"
        check("FTF-0617-I5", f"reviewer role {role.value}: {got!r}, want {want!r}", got == want)
    for label, identity in (
        ("revoked", (IDP, "revoked-admin")),
        ("other organization", (IDP, "only-in-other-org")),
        ("workspace-scoped", (IDP, "workspace-scoped-admin")),
        ("unknown", (IDP, "nobody")),
    ):
        check(
            "FTF-0617-I5b",
            f"reviewer {label} authorized",
            code(lambda identity=identity: authorize(identity)) == "human_reviewer_not_authorized",
        )
    check(
        "FTF-0617-I5c",
        "the producer reviewed their own work",
        code(lambda: authorize((IDP, "producer-1"))) == "human_reviewer_not_independent",
    )
    for variant in ("PRODUCER-1", "Producer-1", "producer-1 ", "ｐroducer-1"):
        normal = unicodedata.normalize("NFKC", variant).casefold().strip()
        want = "human_reviewer_not_independent" if normal == "producer-1" else ""
        check(
            "FTF-0617-I5d",
            f"stored variant {variant!r}: {code(lambda variant=variant: authorize((IDP, variant)))!r}, want {want!r}",
            code(lambda variant=variant: authorize((IDP, variant))) == want,
        )
    check(
        "FTF-0617-I5e",
        "an independent reviewer was refused",
        code(lambda: authorize((IDP, "someone-else"))) == "",
    )
    check(
        "FTF-0617-I5f",
        "same subject, other issuer, was treated as the producer",
        code(lambda: authorize(("https://other-idp.test", "producer-1"))) == "",
    )
    check(
        "FTF-0617-I5g",
        "the allow-side lookup normalised the reviewer id",
        code(lambda: authorize((IDP, "SOMEONE-ELSE"))) == "human_reviewer_not_authorized",
    )
    u.reset()
    plan = decide(org_ze, approval=None, human_reviewers=((IDP, "evaluator-1"),))
    check(
        "FTF-0617-I5h",
        f"authorized independent human: {plan.mode.value}",
        plan.mode.value == "human_review",
    )
    plan = decide(org_ze, approval=None, human_reviewers=((IDP, "producer-1"), (IDP, "viewer-1")))
    check(
        "FTF-0617-I5i",
        f"producer + viewer only: {plan.mode.value}/{plan.reason}",
        plan.mode.value == "blocked" and plan.reason == "judge_capability_unavailable",
    )

    # -- I6: end to end: private plan, verdict, then the identity changing under the policy ------------
    u.reset()
    plan = decide(org_ze)
    check(
        "FTF-0617-I6",
        f"valid approval gave {plan.mode.value}/{plan.approval_defect}",
        plan.mode.value == "private_llm_judge" and plan.reason == "private_judge_attested",
    )
    check(
        "FTF-0617-I6b",
        "admission made exactly one request and it carried no candidate content",
        len(u.G.requests) == 1 and u.G.candidate_requests == 0,
    )

    def run(plan, org_id=org_ze, **kw):
        kwargs = {
            "job_profile": None,
            "rubric": u.h._RUBRIC,
            "calibration": u.CALIBRATION,
            "candidate_model_id": "candidate-model-v1",
            "candidate_response": u.CANDIDATE,
            "secret_config": u.h._SECRET_CONFIG,
            "policy": u.h._policy_for(u.PORT),
            "now": u.NOW,
        }
        kwargs.update(kw)
        with tenant_scoped_session(factory, org_id) as session:
            return m.run_private_judge(
                plan, session=session, context=TenantContext(org_id), **kwargs
            )

    u.G.requests.clear()
    outcome = run(plan)
    check(
        "FTF-0617-I6c",
        f"verdict blocked: {outcome.code}",
        not outcome.blocked and outcome.passed is True,
    )
    check(
        "FTF-0617-I6d",
        "the verdict carries the approved identity",
        outcome.result is not None
        and outcome.result.resolved_model == "judge-model-v1"
        and outcome.result.deployment_fingerprint == "dep-A",
    )
    u.G.requests.clear()
    u.G.judge_fp = "dep-B"
    outcome = run(plan)
    check(
        "FTF-0617-I6e",
        f"fingerprint change: {(outcome.blocked, outcome.code, outcome.possible_egress)}",
        outcome.blocked and outcome.code == "judge_route_changed" and outcome.possible_egress,
    )
    u.G.judge_fp = "dep-A"
    # The profile is re-read from the database: the project is weakened under a standard organization.
    with Session(owner_engine) as owner:
        owner.execute(
            text("UPDATE organizations SET default_security_profile = 'standard' WHERE id = :id"),
            {"id": org_ze},
        )
        owner.execute(
            text("UPDATE projects SET security_profile = 'standard' WHERE id = :id"),
            {"id": project_ze},
        )
        owner.commit()
    u.G.requests.clear()
    check(
        "FTF-0617-I6f",
        "a plan survived its organization leaving the strict profile",
        code(lambda: run(plan)) == "judge_plan_invalid" and not u.G.requests,
    )
    with Session(owner_engine) as owner:
        owner.execute(
            text(
                "UPDATE organizations SET default_security_profile = 'zero_egress' WHERE id = :id"
            ),
            {"id": org_ze},
        )
        owner.commit()
    check(
        "FTF-0617-I6g",
        "a plan was run under another organization's context",
        code(lambda: run(plan, org_id=org_std)) == "judge_plan_invalid",
    )

    # -- I7: the policy wrote nothing ---------------------------------------------------------------------
    after = counts()
    check("FTF-0617-I7", f"row counts changed: {before} -> {after}", before == after)

    _finish()


def _finish() -> None:
    if FAILURES:
        print("\n".join(FAILURES))
        print(f"{len(FAILURES)} FTF-0617 integration check(s) failed")
        sys.exit(1)
    print("FTF-0617 integration: all checks passed")


if __name__ == "__main__":
    main()
