#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg,index,union-attr,list-item"
# ruff: noqa: E501
"""FTF-0603-F1 -- server-side suite lock validation (no-DB unit/route/fault tests).

Runnable without Postgres: the REAL suite routes run on a real uvicorn server
over the REAL CRUD and lock-gate modules, against SQLite built from the real ORM
tables (only the Postgres tenant guard, the ownership probe and the audit writer
are replaced). The live-database evidence (row-level security on the new table,
grants, the CHECKs, composite foreign keys, cross-tenant denial on raw SQL, the
migration round trip) is `tests/integration/test_ftf_0603_f1_suite_lock_gate.py`.

Criteria (QUEUE.md rulings block 000, "FTF-0603 (route 1, split)"):
  N1  the assigned reviewer POSTing /lock directly on an all-TBD suite is refused
      409 with one typed finding per violated clause, and the suite stays draft
      with no plan recorded.
  N2  each clause alone refuses: missing critical category, insufficient sample
      plan (missing plan, each floor incl. the final 1000/100 floors), unresolved
      expected output; plus no cases, independence counts by group_key, the
      per-category sample, a missing / stale reviewed hash, a non-canonical
      number. Boundaries (exactly at a floor) pass.
  A1  a valid development and a valid final suite lock (200) with the exact
      response body, the plan row recorded once with the observed counts and
      the hash, the audit event carrying the hash, GET/list returning it.
  H1  suite_sha256 equals the shared vector
      (packages/contracts/test-vectors/evaluation-suite-sha256.json) produced by
      an independent JavaScript reference; organization, project and every
      graded field are bound; case order does not matter; ECMAScript number
      strings; integers outside the IEEE-754 safe range are refused.
  P1  request parsing: every shape error is a 400 naming the field, never the
      value; >200 categories is refused, never truncated; boundaries.
  R1  FTF-0601's checks still run first and unchanged (no reviewer 409,
      final self-review 409, non-member 400, non-reviewer actor 403, already
      locked 409, foreign suite 404).
  L1  the suite row is taken FOR UPDATE before the cases are read.
  M1  migration 0039 shape: single head, ENABLE+FORCE RLS, non-allow-all
      tenant policy, SELECT/INSERT only, CHECKs, composite FKs; ORM mirrors it.
  K1  06-interface.yaml / 06-domain.schema.json declare the lock body, the
      typed 409 and suite_sha256 additively.

Run directly: python3 tests/tasks/test_ftf_0603_f1.py
"""

from __future__ import annotations

import contextlib
import http.client
import json
import re
import socket
import sys
import threading
import time
import types
import urllib.parse
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
import yaml
from fastapi import FastAPI
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.auth.resource_authorization import ResourceAuthorizationError  # noqa: E402
from app.evaluation import evaluation_suite_crud as crud  # noqa: E402
from app.evaluation import evaluation_suite_lock_gate as gate  # noqa: E402
from app.evaluation import evaluation_suite_routes as routes  # noqa: E402
from app.evaluation.evaluation_case_store import EvaluationCaseRecord  # noqa: E402
from app.evaluation.evaluation_suite_schema import EvaluationSuite  # noqa: E402
from app.projects.project_schema import Project  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0603-F1"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


MIGRATION_PATH = ROOT / "apps" / "api" / "alembic" / "versions" / "0039_eval_suite_sample_plans.py"
VERSIONS_DIR = MIGRATION_PATH.parent
INTERFACE_PATH = ROOT / "packages" / "contracts" / "openapi" / "06-interface.yaml"
DOMAIN_PATH = ROOT / "packages" / "contracts" / "jsonschema" / "06-domain.schema.json"
VECTOR_PATH = ROOT / "packages" / "contracts" / "test-vectors" / "evaluation-suite-sha256.json"

# -- harness: real routes + real CRUD + real gate on SQLite -------------------------

_ORG = uuid.UUID("a1111111-1111-4111-8111-11111111111a")
_OTHER_ORG = uuid.UUID("b2222222-2222-4222-8222-22222222222b")
_PROJECT = uuid.UUID("c3333333-3333-4333-8333-33333333333c")
_OTHER_PROJECT = uuid.UUID("d4444444-4444-4444-8444-44444444444d")
_ISSUER = "https://issuer.test"

_SUBJECT_ROLES: dict[str, Role] = {
    "creator-1": Role.EVALUATOR,
    "reviewer-1": Role.WORKSPACE_ADMIN,
    "viewer-1": Role.VIEWER,
}
_PRINCIPALS = {
    key: AuthenticatedPrincipal(issuer=_ISSUER, subject=key)
    for key in ("creator-1", "reviewer-1", "viewer-1", "outsider-1")
}


def _principal_resolver(request: Any) -> AuthenticatedPrincipal | None:
    return _PRINCIPALS.get(request.headers.get("x-test-principal", ""))


def _role_lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
    return _SUBJECT_ROLES.get(subject) if organization_id == _ORG else None


ENGINE = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})


# pysqlite's own transaction handling breaks SAVEPOINT; SQLAlchemy's documented
# recipe makes SQLite honour the gate's `begin_nested()` like Postgres does.
@event.listens_for(ENGINE, "connect")
def _sqlite_autocommit(dbapi_connection: Any, _record: Any) -> None:
    dbapi_connection.isolation_level = None


@event.listens_for(ENGINE, "begin")
def _sqlite_begin(connection: Any) -> None:
    connection.exec_driver_sql("BEGIN")


EvaluationSuite.__table__.create(ENGINE)
EvaluationCaseRecord.__table__.create(ENGINE)
gate.EvaluationSuiteSamplePlan.__table__.create(ENGINE)
AUDIT: list[dict[str, Any]] = []
STATEMENTS: list[Any] = []


def _write_audit(session: Session, **kwargs: Any) -> None:
    session.info.setdefault("audit", []).append(kwargs)


def _passthrough_execute(session: Session, organization_id: uuid.UUID, statement: Any) -> Any:
    return session.execute(statement)


def _gate_execute(session: Session, organization_id: uuid.UUID, statement: Any) -> Any:
    STATEMENTS.append(statement)
    return session.execute(statement)


def _authorize(
    session: Session, context: Any, table: Any, id_column: Any, org_column: Any, resource_id: Any
) -> None:
    if table is Project.__table__:
        # `projects` uses Postgres-only JSONB, so its ownership is a fixed map here.
        if (resource_id, context.organization_id) not in {(_PROJECT, _ORG), (_OTHER_PROJECT, _ORG)}:
            raise ResourceAuthorizationError("denied")
        return
    row = session.execute(select(org_column).where(id_column == resource_id)).first()
    if row is None or row[0] != context.organization_id:
        raise ResourceAuthorizationError("denied")


@contextlib.contextmanager
def _scoped_session(session_factory: Any, tenant_context: Any):
    session = Session(ENGINE)
    try:
        yield session
        session.commit()
        AUDIT.extend(session.info.get("audit", []))
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


crud.execute_tenant_scoped = _passthrough_execute
crud.authorize_resource_ownership = _authorize
crud.write_audit_event = _write_audit
gate.execute_tenant_scoped = _gate_execute
gate.write_audit_event = _write_audit
routes.tenant_scoped_session_for = _scoped_session


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@contextlib.contextmanager
def _serve(app: FastAPI):
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(250):
        if server.started:
            break
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


class _Http:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body)


_SERVER: dict[str, Any] = {}


def _base() -> str:
    if "base" not in _SERVER:
        app = FastAPI()
        app.include_router(
            routes.build_evaluation_suite_router(
                lambda: None, principal_resolver=_principal_resolver, role_lookup=_role_lookup
            )
        )
        manager = _serve(app)
        _SERVER["manager"] = manager
        _SERVER["base"] = manager.__enter__()
    return str(_SERVER["base"])


def _stop_server() -> None:
    manager = _SERVER.pop("manager", None)
    _SERVER.pop("base", None)
    if manager is not None:
        manager.__exit__(None, None, None)


def _request(
    method: str,
    path: str,
    *,
    principal: str | None = "reviewer-1",
    payload: Any = None,
    raw: bytes | None = None,
) -> _Http:
    headers: dict[str, str] = {}
    if principal is not None:
        headers["X-Test-Principal"] = principal
    body = raw
    if raw is None and payload is not None:
        body = json.dumps(payload).encode("utf-8")
    if body is not None:
        headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(_base())
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=30)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return _Http(response.status, response.read())
    finally:
        conn.close()


def _suite_path(suite: uuid.UUID, *, org: uuid.UUID = _ORG, project: uuid.UUID = _PROJECT) -> str:
    return f"/api/v1/organizations/{org}/projects/{project}/evaluation-suites/{suite}"


def _lock(suite: uuid.UUID, payload: Any = None, **kwargs: Any) -> _Http:
    return _request("POST", _suite_path(suite, **kwargs) + "/lock", payload=payload)


_NOW = datetime(2026, 11, 27, 12, 0, 0, tzinfo=UTC)
_VERSION = [0]


def _seed_suite(
    *,
    purpose: str = "development",
    reviewer: str | None = "reviewer-1",
    created_by: str = "creator-1",
    org: uuid.UUID = _ORG,
    project: uuid.UUID = _PROJECT,
) -> uuid.UUID:
    _VERSION[0] += 1
    suite = EvaluationSuite(
        id=uuid.uuid4(),
        organization_id=org,
        project_id=project,
        agent_manifest_id=uuid.uuid4(),
        version=_VERSION[0],
        status="draft",
        purpose=purpose,
        name="suite",
        grading_logic_version=1,
        reviewer_id=reviewer,
        created_by=created_by,
        created_at=_NOW,
    )
    suite_id: uuid.UUID = suite.id
    with Session(ENGINE) as session:
        session.add(suite)
        session.commit()
    return suite_id


def _seed_cases(suite_id: uuid.UUID, cases: list[dict[str, Any]]) -> None:
    with Session(ENGINE) as session:
        suite = session.get(EvaluationSuite, suite_id)
        for raw in cases:
            session.add(
                EvaluationCaseRecord(
                    id=uuid.uuid4(),
                    organization_id=suite.organization_id,
                    project_id=suite.project_id,
                    suite_id=suite_id,
                    created_by="creator-1",
                    created_at=_NOW,
                    updated_at=_NOW,
                    **raw,
                )
            )
        session.commit()


def _case(key: str, **overrides: Any) -> dict[str, Any]:
    case: dict[str, Any] = {
        "case_key": key,
        "category": "prompt injection",
        "severity": "high",
        "critical": True,
        "language": "en",
        "group_key": f"group-{key}",
        "input_json": {"text": f"input {key}"},
        "expected_json": {"refused": True},
        "grader_type": "required_refusal",
        "grader_config_json": {},
        "weight": 1.0,
    }
    case.update(overrides)
    return case


def _current_sha(suite_id: uuid.UUID) -> str:
    with Session(ENGINE) as session:
        suite = session.get(EvaluationSuite, suite_id)
        cases = list(
            session.execute(
                select(EvaluationCaseRecord).where(EvaluationCaseRecord.suite_id == suite_id)
            ).scalars()
        )
        return gate.compute_suite_sha256(suite, cases)


def _plan(
    categories: list[str] | None = None, tasks: int = 1, critical: int = 0, per: int = 1
) -> dict[str, Any]:
    return {
        "required_categories": ["prompt injection"] if categories is None else categories,
        "min_independent_tasks": tasks,
        "min_critical_scenarios": critical,
        "min_cases_per_category": per,
    }


def _body(
    suite_id: uuid.UUID, plan: dict[str, Any] | None = None, sha: str | None = "current"
) -> dict[str, Any]:
    body: dict[str, Any] = {"sample_plan": _plan() if plan is None else plan}
    if sha == "current":
        body["suite_sha256"] = _current_sha(suite_id)
    elif sha is not None:
        body["suite_sha256"] = sha
    return body


def _findings(response: _Http) -> list[dict[str, Any]] | None:
    try:
        detail = response.json()["detail"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(detail, dict) or detail.get("code") != "suite_lock_validation_failed":
        return None
    return detail.get("findings")


def _f(
    code: str, subject: str | None = None, need: int | None = None, have: int | None = None
) -> dict[str, Any]:
    return {"code": code, "subject": subject, "need": need, "have": have}


def _state(suite_id: uuid.UUID) -> tuple[str, int]:
    with Session(ENGINE) as session:
        status = session.get(EvaluationSuite, suite_id).status
        plans = session.execute(
            select(gate.EvaluationSuiteSamplePlan).where(
                gate.EvaluationSuiteSamplePlan.suite_id == suite_id
            )
        ).all()
        return status, len(plans)


def _refused(
    name: str, suite_id: uuid.UUID, response: _Http, expected: list[dict[str, Any]]
) -> None:
    check(
        "N2",
        f"{name}: 409 with exactly the typed findings {expected} (got {response.status} {response.body[:400]!r})",
        response.status == 409 and _findings(response) == expected,
    )
    check(
        "N2",
        f"{name}: the suite is still draft and no plan was recorded",
        _state(suite_id) == ("draft", 0),
    )


# -- N1: all-TBD suite, direct POST by the assigned reviewer -------------------------


def test_n1_all_tbd_final_suite_is_refused_for_every_clause() -> None:
    suite_id = _seed_suite(purpose="final")
    _seed_cases(
        suite_id,
        [
            _case("a", critical=False, grader_type="exact_match", expected_json={"answer": "TBD"}),
            _case("b", critical=False, grader_type="exact_match", expected_json={"answer": "tbd"}),
            _case(
                "c", critical=False, grader_type="exact_match", expected_json={"answer": " TODO "}
            ),
        ],
    )
    response = _lock(
        suite_id, _body(suite_id, _plan(["prompt injection"], tasks=10, critical=1, per=2))
    )
    check(
        "N1",
        "an all-TBD final suite locked directly by its assigned reviewer is refused for every clause",
        response.status == 409
        and _findings(response)
        == [
            _f("sample_plan_below_floor", "min_independent_tasks", 1000, 10),
            _f("sample_plan_below_floor", "min_critical_scenarios", 100, 1),
            _f("insufficient_independent_tasks", None, 10, 3),
            _f("insufficient_critical_scenarios", None, 1, 0),
            _f("missing_critical_category", "prompt injection"),
            _f("unresolved_expected_output", "a"),
            _f("unresolved_expected_output", "b"),
            _f("unresolved_expected_output", "c"),
        ],
    )
    check(
        "N1",
        "the all-TBD suite is still draft with no plan recorded",
        _state(suite_id) == ("draft", 0),
    )
    got = _request("GET", _suite_path(suite_id))
    check(
        "N1",
        "GET still shows draft, locked_at null and suite_sha256 null after the refusal",
        got.status == 200
        and got.json()["status"] == "draft"
        and got.json()["locked_at"] is None
        and got.json()["suite_sha256"] is None,
    )
    check(
        "N1",
        "a refusal body names codes and case keys only, never an expected value",
        b"TBD" not in response.body and b"TODO" not in response.body,
    )


# -- N2: each clause alone -------------------------------------------------------------


def _valid_dev_suite(*cases: dict[str, Any]) -> uuid.UUID:
    suite_id = _seed_suite()
    _seed_cases(suite_id, list(cases) or [_case("k1")])
    return suite_id


def test_n2_missing_plan_and_missing_hash() -> None:
    suite_id = _valid_dev_suite()
    response = _request("POST", _suite_path(suite_id) + "/lock")
    _refused(
        "no body at all",
        suite_id,
        response,
        [_f("sample_plan_missing"), _f("suite_sha256_required")],
    )
    response = _lock(suite_id, {"suite_sha256": _current_sha(suite_id)})
    _refused("a hash but no plan", suite_id, response, [_f("sample_plan_missing")])
    response = _lock(suite_id, {"sample_plan": None, "suite_sha256": _current_sha(suite_id)})
    _refused("an explicit null plan", suite_id, response, [_f("sample_plan_missing")])
    response = _lock(suite_id, _body(suite_id, sha=None))
    _refused("a plan but no hash", suite_id, response, [_f("suite_sha256_required")])


def test_n2_plan_floors_development() -> None:
    suite_id = _valid_dev_suite()
    _refused(
        "no required category",
        suite_id,
        _lock(suite_id, _body(suite_id, _plan([]))),
        [_f("sample_plan_below_floor", "required_categories", 1, 0)],
    )
    _refused(
        "min_independent_tasks 0",
        suite_id,
        _lock(suite_id, _body(suite_id, _plan(tasks=0))),
        [_f("sample_plan_below_floor", "min_independent_tasks", 1, 0)],
    )
    _refused(
        "min_cases_per_category 0",
        suite_id,
        _lock(suite_id, _body(suite_id, _plan(per=0))),
        [_f("sample_plan_below_floor", "min_cases_per_category", 1, 0)],
    )


def _final_suite(groups: int = 1000, critical: int = 100) -> uuid.UUID:
    suite_id = _seed_suite(purpose="final")
    cases = []
    for i in range(groups):
        if i < critical:
            cases.append(_case(f"k{i:04d}", group_key=f"g{i}"))
        else:
            cases.append(
                _case(
                    f"k{i:04d}",
                    group_key=f"g{i}",
                    critical=False,
                    grader_type="exact_match",
                    expected_json={"answer": "ok"},
                )
            )
    _seed_cases(suite_id, cases)
    return suite_id


def test_n2_final_floors_and_a_valid_final_lock() -> None:
    suite_id = _final_suite()
    _refused(
        "final plan with 999 independent tasks",
        suite_id,
        _lock(suite_id, _body(suite_id, _plan(tasks=999, critical=100))),
        [_f("sample_plan_below_floor", "min_independent_tasks", 1000, 999)],
    )
    _refused(
        "final plan with 99 critical scenarios",
        suite_id,
        _lock(suite_id, _body(suite_id, _plan(tasks=1000, critical=99))),
        [_f("sample_plan_below_floor", "min_critical_scenarios", 100, 99)],
    )
    response = _lock(suite_id, _body(suite_id, _plan(tasks=1000, critical=100)))
    check(
        "A1",
        f"a final suite exactly at the 1000/100 floors with a distinct reviewer locks (200), got {response.status} {response.body[:300]!r}",
        response.status == 200 and response.json()["status"] == "locked",
    )
    check("A1", "the final suite's plan row is recorded once", _state(suite_id) == ("locked", 1))


def test_n2_final_suite_below_floor_on_content() -> None:
    suite_id = _final_suite(groups=999, critical=99)
    _refused(
        "final suite with 999 groups / 99 critical against a 1000/100 plan",
        suite_id,
        _lock(suite_id, _body(suite_id, _plan(tasks=1000, critical=100))),
        [
            _f("insufficient_independent_tasks", None, 1000, 999),
            _f("insufficient_critical_scenarios", None, 100, 99),
        ],
    )


def test_n2_missing_critical_category() -> None:
    suite_id = _valid_dev_suite(
        _case("k1"),
        _case(
            "k2",
            category="tool timeout",
            critical=False,
            grader_type="exact_match",
            expected_json={"answer": "retry"},
        ),
    )
    _refused(
        "a required category with only non-critical cases",
        suite_id,
        _lock(suite_id, _body(suite_id, _plan(["prompt injection", "tool timeout"]))),
        [_f("missing_critical_category", "tool timeout")],
    )
    _refused(
        "a required category with no case at all",
        suite_id,
        _lock(suite_id, _body(suite_id, _plan(["prompt injection", "loop prevention"]))),
        [
            _f("missing_critical_category", "loop prevention"),
            _f("insufficient_category_sample", "loop prevention", 1, 0),
        ],
    )
    _refused(
        "categories match exactly (no case/whitespace folding)",
        suite_id,
        _lock(suite_id, _body(suite_id, _plan(["Prompt Injection"]))),
        [
            _f("missing_critical_category", "Prompt Injection"),
            _f("insufficient_category_sample", "Prompt Injection", 1, 0),
        ],
    )


def test_n2_independence_counts_groups_not_rows() -> None:
    suite_id = _valid_dev_suite(
        _case("k1", group_key="g1"),
        _case("k2", group_key="g1"),
        _case(
            "k3",
            group_key="g2",
            category="other",
            critical=False,
            grader_type="exact_match",
            expected_json={"answer": "x"},
        ),
    )
    _refused(
        "three rows in two groups against min_independent_tasks 3",
        suite_id,
        _lock(suite_id, _body(suite_id, _plan(tasks=3))),
        [_f("insufficient_independent_tasks", None, 3, 2)],
    )
    _refused(
        "two critical rows in one group against min_critical_scenarios 2",
        suite_id,
        _lock(suite_id, _body(suite_id, _plan(critical=2))),
        [_f("insufficient_critical_scenarios", None, 2, 1)],
    )
    _refused(
        "two rows of one group in the category against min_cases_per_category 2",
        suite_id,
        _lock(suite_id, _body(suite_id, _plan(per=2))),
        [_f("insufficient_category_sample", "prompt injection", 2, 1)],
    )
    response = _lock(suite_id, _body(suite_id, _plan(tasks=2, critical=1, per=1)))
    check(
        "N2",
        f"exactly at every count (2 groups, 1 critical group, 1 per category) the suite locks, got {response.status} {response.body[:300]!r}",
        response.status == 200,
    )


def test_n2_unresolved_expected_output() -> None:
    suite_id = _valid_dev_suite(
        _case("k1"),
        _case("k2", expected_json={"refused": True, "reason": " tbd "}),
        _case("k3", expected_json={"steps": []}),
    )
    _refused(
        "placeholder and empty expected outputs",
        suite_id,
        _lock(suite_id, _body(suite_id)),
        [_f("unresolved_expected_output", "k2"), _f("unresolved_expected_output", "k3")],
    )


def test_n2_no_cases() -> None:
    suite_id = _seed_suite()
    _refused("a suite with no case", suite_id, _lock(suite_id, _body(suite_id)), [_f("no_cases")])


def test_n2_stale_and_wrong_hash() -> None:
    suite_id = _valid_dev_suite(_case("k1"))
    reviewed = _current_sha(suite_id)
    _refused(
        "a well-formed hash of something else",
        suite_id,
        _lock(suite_id, _body(suite_id, sha="0123456789abcdef" * 4)),
        [_f("suite_sha256_mismatch")],
    )
    _seed_cases(suite_id, [_case("k2")])
    _refused(
        "a hash reviewed before a case was added",
        suite_id,
        _lock(suite_id, _body(suite_id, sha=reviewed)),
        [_f("suite_sha256_mismatch")],
    )


def test_n2_non_canonical_number() -> None:
    suite_id = _valid_dev_suite(_case("k1"), _case("k2", input_json={"n": 2**53}))
    _refused(
        "an integer beyond 2**53-1 in a case payload",
        suite_id,
        _lock(suite_id, _body(suite_id, sha="0123456789abcdef" * 4)),
        [_f("non_canonical_number", "k2")],
    )


# -- A1: valid locks, exact body, recorded plan, audit, GET/list -------------------------


def test_a1_valid_development_lock_records_plan_and_hash() -> None:
    suite_id = _valid_dev_suite(
        _case("k1"),
        _case("k2", category="tool timeout", group_key="g-t"),
        _case(
            "k3",
            category="tool timeout",
            group_key="g-t",
            critical=False,
            grader_type="exact_match",
            expected_json={"answer": "retry"},
        ),
    )
    draft_id = _seed_suite()
    sha = _current_sha(suite_id)
    AUDIT.clear()
    response = _lock(
        suite_id,
        _body(suite_id, _plan(["tool timeout", "prompt injection"], tasks=2, critical=2, per=1)),
    )
    with Session(ENGINE) as session:
        suite = session.get(EvaluationSuite, suite_id)
        rows = (
            session.execute(
                select(gate.EvaluationSuiteSamplePlan).where(
                    gate.EvaluationSuiteSamplePlan.suite_id == suite_id
                )
            )
            .scalars()
            .all()
        )
        expected_body = {
            "id": str(suite.id),
            "organization_id": str(_ORG),
            "project_id": str(_PROJECT),
            "agent_manifest_id": str(suite.agent_manifest_id),
            "version": suite.version,
            "status": "locked",
            "purpose": "development",
            "name": "suite",
            "grading_logic_version": 1,
            "reviewer_id": "reviewer-1",
            "created_by": "creator-1",
            "suite_sha256": sha,
        }

    def same(body: Any) -> bool:
        # SQLite drops the zone on reload, so timestamps are checked by value.
        if not isinstance(body, dict):
            return False
        rest = {k: v for k, v in body.items() if k not in ("locked_at", "created_at")}
        try:
            locked_at = datetime.fromisoformat(body["locked_at"])
        except (KeyError, TypeError, ValueError):
            return False
        return (
            rest == expected_body
            and abs(
                locked_at.replace(tzinfo=None) - datetime.now(UTC).replace(tzinfo=None)
            ).total_seconds()
            < 300
            and str(body.get("created_at", "")).startswith("2026-11-27T12:00:00")
        )

    check(
        "A1",
        f"a valid development lock returns 200 with exactly the serialized suite and its suite_sha256 (got {response.status} {response.body[:400]!r})",
        response.status == 200 and same(response.json()),
    )
    check("A1", "exactly one plan row was recorded", len(rows) == 1)
    row = rows[0] if rows else None
    check(
        "A1",
        "the plan row records the plan as sent, the observed counts and the hash",
        row is not None
        and row.organization_id == _ORG
        and row.project_id == _PROJECT
        and row.required_categories == ["tool timeout", "prompt injection"]
        and (row.min_independent_tasks, row.min_critical_scenarios, row.min_cases_per_category)
        == (2, 2, 1)
        and (row.case_count, row.independent_task_count, row.critical_scenario_count) == (3, 2, 2)
        and row.suite_sha256 == sha
        and row.created_by == "reviewer-1"
        and row.created_at is not None,
    )
    locks = [a for a in AUDIT if a.get("action") == "evaluation_suite.lock"]
    records = [a for a in AUDIT if a.get("action") == "evaluation_suite.sample_plan_record"]
    check(
        "A1",
        "FTF-0601's own lock audit event is still written once",
        len(locks) == 1 and locks[0]["actor_id"] == "reviewer-1",
    )
    check(
        "A1",
        f"one sample-plan audit event records the hash ({records})",
        len(records) == 1
        and records[0]
        == {
            "organization_id": _ORG,
            "actor_type": "user",
            "actor_id": "reviewer-1",
            "action": "evaluation_suite.sample_plan_record",
            "resource_type": "evaluation_suite",
            "resource_id": str(suite_id),
            "request_id": locks[0]["request_id"] if locks else None,
            "details": {"suite_sha256": sha},
        },
    )
    got = _request("GET", _suite_path(suite_id), principal="viewer-1")
    check(
        "A1",
        "GET returns the locked suite with its suite_sha256",
        got.status == 200 and same(got.json()),
    )
    listed = _request(
        "GET",
        f"/api/v1/organizations/{_ORG}/projects/{_PROJECT}/evaluation-suites",
        principal="viewer-1",
    )
    by_id = {item["id"]: item for item in listed.json()} if listed.status == 200 else {}
    check(
        "A1",
        "list returns each suite's own hash: the locked one's, null for a draft",
        same(by_id.get(str(suite_id)))
        and by_id.get(str(draft_id), {}).get("suite_sha256", "x") is None,
    )
    again = _lock(suite_id, _body(suite_id))
    check(
        "R1",
        "re-locking a locked suite is still FTF-0601's 409 and records nothing more",
        again.status == 409 and _findings(again) is None and _state(suite_id) == ("locked", 1),
    )


# -- R1: FTF-0601's checks run first, unchanged ---------------------------------------


def test_r1_ftf_0601_checks_still_run_first() -> None:
    no_reviewer = _valid_dev_suite()
    with Session(ENGINE) as session:
        session.get(EvaluationSuite, no_reviewer).reviewer_id = None
        session.commit()
    response = _lock(no_reviewer, _body(no_reviewer))
    check(
        "R1",
        "no reviewer: 409 (FTF-0601), not a gate finding",
        response.status == 409 and _findings(response) is None,
    )

    blank = _valid_dev_suite()
    with Session(ENGINE) as session:
        session.get(EvaluationSuite, blank).reviewer_id = "   "
        session.commit()
    response = _lock(blank, _body(blank))
    check(
        "R1",
        "a whitespace-only reviewer: 409 (FTF-0601)",
        response.status == 409 and _findings(response) is None,
    )

    self_review = _seed_suite(purpose="final", reviewer="reviewer-1", created_by=" Reviewer-1 ")
    _seed_cases(self_review, [_case("k1")])
    response = _lock(self_review, _body(self_review))
    check(
        "R1",
        "final self-review: 409 (FTF-0601)",
        response.status == 409 and _findings(response) is None,
    )

    dev_self = _seed_suite(reviewer="reviewer-1", created_by="reviewer-1")
    _seed_cases(dev_self, [_case("k1")])
    response = _lock(dev_self, _body(dev_self))
    check(
        "R1",
        "a development suite may be locked by its own creator-reviewer (200)",
        response.status == 200,
    )

    non_member = _seed_suite(reviewer="ghost-1")
    _seed_cases(non_member, [_case("k1")])
    response = _request(
        "POST", _suite_path(non_member) + "/lock", principal="reviewer-1", payload=_body(non_member)
    )
    check("R1", "a non-member reviewer: 400 (FTF-0601)", response.status == 400)

    valid = _valid_dev_suite()
    response = _request(
        "POST", _suite_path(valid) + "/lock", principal="creator-1", payload=_body(valid)
    )
    check(
        "R1",
        "a write-role actor who is not the reviewer: 403 (FTF-0601)",
        response.status == 403 and _state(valid) == ("draft", 0),
    )
    response = _request(
        "POST", _suite_path(valid) + "/lock", principal="viewer-1", payload=_body(valid)
    )
    check("R1", "a viewer: 403", response.status == 403)
    response = _request("POST", _suite_path(valid) + "/lock", principal=None, payload=_body(valid))
    check("R1", "no principal: 401", response.status == 401)

    foreign = _seed_suite(org=_OTHER_ORG)
    response = _lock(foreign, {})
    check(
        "R1",
        "a suite of another organization: 404 not_found",
        response.status == 404 and response.json() == {"detail": "not_found"},
    )
    response = _lock(valid, {}, project=_OTHER_PROJECT)
    check(
        "R1",
        "a suite under another project path: 404 not_found",
        response.status == 404 and response.json() == {"detail": "not_found"},
    )
    response = _request("GET", _suite_path(foreign))
    check(
        "R1",
        "GET of a foreign suite: 404 not_found",
        response.status == 404 and response.json() == {"detail": "not_found"},
    )
    response = _request(
        "GET", f"/api/v1/organizations/{_ORG}/projects/{uuid.uuid4()}/evaluation-suites"
    )
    check("R1", "list of a missing project: 404 not_found", response.status == 404)


# -- P1: request parsing ---------------------------------------------------------------


def _bad(payload: Any, field: str) -> None:
    try:
        gate.parse_lock_request(payload)
    except gate.LockRequestError as exc:
        check(
            "P1", f"{payload!r:.80} names field {field!r} (got {exc.field!r})", exc.field == field
        )
        return
    check("P1", f"{payload!r:.80} is refused as {field!r}", False)


def test_p1_parse_valid_request() -> None:
    sha = "0123456789abcdef" * 4
    parsed = gate.parse_lock_request(
        {"sample_plan": _plan(["a", "b"], 3, 2, 1), "suite_sha256": sha}
    )
    check(
        "P1",
        "a full body parses to the exact plan and hash",
        parsed
        == gate.LockRequest(
            sample_plan=gate.SamplePlan(("a", "b"), 3, 2, 1),
            suite_sha256=sha,
        ),
    )
    check(
        "P1",
        "an empty body is no plan and no hash",
        gate.parse_lock_request({}) == gate.LockRequest(None, None),
    )
    limits = gate.parse_sample_plan(
        _plan(["c" * 100] + [f"x{i}" for i in range(199)], 0, 0, gate.MAX_PLAN_COUNT)
    )
    check(
        "P1",
        "boundaries accepted: 200 categories, a 100-character category, counts 0 and 1,000,000",
        len(limits.required_categories) == 200
        and limits.min_independent_tasks == 0
        and limits.min_critical_scenarios == 0
        and limits.min_cases_per_category == gate.MAX_PLAN_COUNT,
    )


def test_p1_parse_refusals() -> None:
    sha = "0123456789abcdef" * 4
    _bad({"sample_plan": _plan(), "suite_sha256": sha, "extra": 1}, "body")
    _bad({"suite_sha256": sha.upper()}, "suite_sha256")
    _bad({"suite_sha256": sha[:-1]}, "suite_sha256")
    _bad({"suite_sha256": sha + "0"}, "suite_sha256")
    _bad({"suite_sha256": "g" * 64}, "suite_sha256")
    _bad({"suite_sha256": 5}, "suite_sha256")
    _bad({"sample_plan": "plan"}, "sample_plan")
    _bad({"sample_plan": [1]}, "sample_plan")
    missing = _plan()
    del missing["min_cases_per_category"]
    _bad({"sample_plan": missing}, "sample_plan")
    _bad({"sample_plan": {**_plan(), "notes": "x"}}, "sample_plan")
    _bad({"sample_plan": _plan(categories="prompt injection")}, "sample_plan.required_categories")
    _bad({"sample_plan": _plan([f"x{i}" for i in range(201)])}, "sample_plan.required_categories")
    for category in ("", " a", "a ", 7, None, "c" * 101):
        _bad({"sample_plan": _plan(["ok", category])}, "sample_plan.required_categories")
    _bad({"sample_plan": _plan(["a", "a"])}, "sample_plan.required_categories")
    for name in ("min_independent_tasks", "min_critical_scenarios", "min_cases_per_category"):
        for value in (True, "1", 1.0, -1, gate.MAX_PLAN_COUNT + 1, None):
            _bad({"sample_plan": {**_plan(), name: value}}, f"sample_plan.{name}")


def test_p1_http_shape_errors_are_400_naming_the_field() -> None:
    suite_id = _valid_dev_suite()
    response = _lock(suite_id, {"bogus": "SECRET-VALUE"})
    check(
        "P1",
        "an unknown body key is 400 invalid_lock_request naming 'body', never echoing the value",
        response.status == 400
        and response.json() == {"detail": {"code": "invalid_lock_request", "field": "body"}}
        and b"SECRET-VALUE" not in response.body,
    )
    response = _lock(
        suite_id, {"sample_plan": _plan(tasks=-1), "suite_sha256": _current_sha(suite_id)}
    )
    check(
        "P1",
        "a negative count is 400 naming sample_plan.min_independent_tasks",
        response.status == 400
        and response.json()
        == {
            "detail": {"code": "invalid_lock_request", "field": "sample_plan.min_independent_tasks"}
        },
    )
    response = _request("POST", _suite_path(suite_id) + "/lock", raw=b"{not json")
    check("P1", "a non-JSON body is 400", response.status == 400)
    check(
        "P1", "after every shape error the suite is still draft", _state(suite_id) == ("draft", 0)
    )


# -- floors and content (pure) -----------------------------------------------------------


def test_floor_findings_pure() -> None:
    plan = gate.SamplePlan(("a",), 1, 0, 1)
    check(
        "N2",
        "a development plan at its floors has no finding",
        gate.plan_floor_findings(plan, "development") == [],
    )
    final = gate.SamplePlan(("a",), 1000, 100, 1)
    check(
        "N2",
        "a final plan at 1000/100 has no finding",
        gate.plan_floor_findings(final, "final") == [],
    )
    check(
        "N2",
        "the development floors are not the final ones",
        gate.plan_floor_findings(gate.SamplePlan(("a",), 1, 0, 1), "final")
        == [
            gate.Finding("sample_plan_below_floor", "min_independent_tasks", 1000, 1),
            gate.Finding("sample_plan_below_floor", "min_critical_scenarios", 100, 0),
        ],
    )
    for purpose in ("Final", " final", "FINAL"):
        check(
            "N2",
            f"purpose {purpose!r} is not 'final' (exact match only)",
            gate.plan_floor_findings(gate.SamplePlan(("a",), 1, 0, 1), purpose) == [],
        )
    check(
        "N2",
        "Finding.as_dict is the exact wire shape",
        gate.Finding("c", "s", 2, 1).as_dict()
        == {"code": "c", "subject": "s", "need": 2, "have": 1},
    )


def test_unresolved_markers_pure() -> None:
    for value in (
        None,
        "",
        "  ",
        "TBD",
        " tbd",
        "tbd ",
        "Todo",
        "tbc",
        "Unresolved",
        "FIXME",
        "?",
        [],
        {},
        {"a": [1, {"b": "tbc"}]},
        ["ok", None],
    ):
        check("N2", f"{value!r} is unresolved", gate.has_unresolved_expected(value) is True)
    for value in ("done", "tbd later", 0, 1.5, False, True, {"a": 1}, ["x"], {"a": {"b": [0]}}):
        check("N2", f"{value!r} is resolved", gate.has_unresolved_expected(value) is False)


def test_content_findings_pure() -> None:
    def rec(key: str, **kw: Any) -> Any:
        return types.SimpleNamespace(**_case(key, **kw))

    plan = gate.SamplePlan(("prompt injection",), 1, 1, 1)
    check(
        "N2",
        "a valid case set has no content finding",
        gate.content_findings([rec("k1")], plan) == [],
    )
    check(
        "N2",
        "no plan still checks expected outputs",
        gate.content_findings([rec("k1", expected_json={"a": "tbd"})], None)
        == [gate.Finding("unresolved_expected_output", "k1")],
    )
    check(
        "N2",
        "no case at all is no_cases",
        gate.content_findings([], plan) == [gate.Finding("no_cases")],
    )


# -- H1: canonical hash ------------------------------------------------------------------


def _vector() -> dict[str, Any]:
    return json.loads(VECTOR_PATH.read_text(encoding="utf-8"))


def _vector_objects(vector: dict[str, Any]) -> tuple[Any, list[Any]]:
    data = vector["input"]
    suite = types.SimpleNamespace(
        organization_id=uuid.UUID(data["organization_id"]),
        project_id=uuid.UUID(data["project_id"]),
        id=uuid.UUID(data["suite"]["id"]),
        agent_manifest_id=uuid.UUID(data["suite"]["agent_manifest_id"]),
        version=data["suite"]["version"],
        purpose=data["suite"]["purpose"],
        grading_logic_version=data["suite"]["grading_logic_version"],
    )
    return suite, [types.SimpleNamespace(**case) for case in data["cases"]]


def test_h1_shared_vector() -> None:
    vector = _vector()
    suite, cases = _vector_objects(vector)
    check(
        "H1", "the vector names the hash format the API uses", vector["format"] == gate.HASH_FORMAT
    )
    check(
        "H1",
        "compute_suite_sha256 equals the shared vector's hash (independent JavaScript reference)",
        gate.compute_suite_sha256(suite, cases) == vector["suite_sha256"],
    )
    document = json.loads(vector["canonical_json"])
    check(
        "H1",
        "canonical_json reproduces the vector's canonical text byte for byte",
        gate.canonical_json(document) == vector["canonical_json"],
    )
    check(
        "H1",
        "the hash does not depend on the order cases arrive in",
        gate.compute_suite_sha256(suite, list(reversed(cases))) == vector["suite_sha256"],
    )
    keys = [c["case_key"] for c in document["cases"]]
    check(
        "H1",
        "cases are ordered by UTF-16 code units (U+1F600 before U+FFFF), not code points",
        keys == ["a-happy", "z-refund-blocked", "\U0001f600-astral", "￿-bmp-high"],
    )


def test_h1_scope_and_every_graded_field_is_bound() -> None:
    vector = _vector()
    suite, cases = _vector_objects(vector)
    base = vector["suite_sha256"]
    for field, value in (
        ("organization_id", uuid.uuid4()),
        ("project_id", uuid.uuid4()),
        ("id", uuid.uuid4()),
        ("agent_manifest_id", uuid.uuid4()),
        ("version", 4),
        ("purpose", "development"),
        ("grading_logic_version", 3),
    ):
        changed = types.SimpleNamespace(**{**vars(suite), field: value})
        check(
            "H1",
            f"changing suite {field} changes the hash",
            gate.compute_suite_sha256(changed, cases) != base,
        )
    replacements = {
        "case_key": "a-happy-2",
        "category": "other",
        "severity": "high",
        "critical": True,
        "language": "fr",
        "group_key": "g-other",
        "input_json": {"messages": []},
        "expected_json": {"tool": "x"},
        "grader_type": "tool_name",
        "grader_config_json": {"strict": False},
        "weight": 2.5,
    }
    for field, value in replacements.items():
        changed_cases = [
            types.SimpleNamespace(**{**vars(cases[1]), field: value}),
            cases[0],
            *cases[2:],
        ]
        check(
            "H1",
            f"changing case {field} changes the hash",
            gate.compute_suite_sha256(suite, changed_cases) != base,
        )
    check(
        "H1",
        "dropping a case changes the hash",
        gate.compute_suite_sha256(suite, cases[1:]) != base,
    )
    nameless = types.SimpleNamespace(**{**vars(cases[0]), "name": "ignored", "id": uuid.uuid4()})
    check(
        "H1",
        "attributes outside the graded fields (row id, name) do not enter the hash",
        gate.compute_suite_sha256(suite, [nameless, *cases[1:]]) == base,
    )


# Expected strings produced by Node (JSON.stringify) for the same doubles.
_NUMBERS = [
    (0.0, "0"),
    (-0.0, "0"),
    (1.0, "1"),
    (100.0, "100"),
    (1e20, "100000000000000000000"),
    (1e21, "1e+21"),
    (123.456, "123.456"),
    (0.1, "0.1"),
    (1e-6, "0.000001"),
    (1e-7, "1e-7"),
    (1.5e-7, "1.5e-7"),
    (-0.5, "-0.5"),
    (2.5e-300, "2.5e-300"),
    (1.7976931348623157e308, "1.7976931348623157e+308"),
    (5e-324, "5e-324"),
    (123456789.125, "123456789.125"),
    (0.000123, "0.000123"),
    (-1e21, "-1e+21"),
    (12345678901234567890.0, "12345678901234567000"),
    (1e-5, "0.00001"),
    (0.5, "0.5"),
    (2.0, "2"),
    (123.0, "123"),
    (-1.5e-7, "-1.5e-7"),
]


def test_h1_ecmascript_numbers_and_integer_bounds() -> None:
    for value, expected in _NUMBERS:
        got = gate.canonical_json(value)
        check(
            "H1",
            f"{value!r} serializes as JavaScript does ({expected}), got {got}",
            got == expected,
        )
    check("H1", "2**53-1 is exact", gate.canonical_json(2**53 - 1) == "9007199254740991")
    check("H1", "-(2**53-1) is exact", gate.canonical_json(-(2**53 - 1)) == "-9007199254740991")
    for value in (2**53, -(2**53), float("nan"), float("inf"), float("-inf"), (1, 2), {1, 2}, b"x"):
        try:
            gate.canonical_json(value)
        except gate.NonCanonicalValueError:
            continue
        except Exception as exc:  # noqa: BLE001
            check("H1", f"{value!r} raises NonCanonicalValueError, not {type(exc).__name__}", False)
            continue
        check("H1", f"{value!r} has no canonical form and is refused", False)
    check(
        "H1",
        "literals, strings and containers",
        gate.canonical_json([None, True, False, 'a"\\\n\u0001é', {"b": 1, "a": [], "": {}}])
        == '[null,true,false,"a\\"\\\\\\n\\u0001é",{"":{},"a":[],"b":1}]',
    )


# -- L1: row lock ------------------------------------------------------------------------


def test_l1_suite_row_is_locked_before_cases_are_read() -> None:
    suite_id = _valid_dev_suite(_case("k1"))
    STATEMENTS.clear()
    response = _lock(suite_id, _body(suite_id))
    compiled = [str(s.compile(dialect=postgresql.dialect())) for s in STATEMENTS]
    lock_index = next((i for i, sql in enumerate(compiled) if "FOR UPDATE" in sql), None)
    case_index = next((i for i, sql in enumerate(compiled) if "FROM evaluation_cases" in sql), None)
    check("L1", "the lock succeeded", response.status == 200)
    check(
        "L1",
        "a SELECT ... FROM evaluation_suites ... FOR UPDATE runs before the cases are read",
        lock_index is not None
        and "FROM evaluation_suites" in compiled[lock_index]
        and case_index is not None
        and lock_index < case_index,
    )


def test_l1_lock_reloads_the_row_under_the_lock() -> None:
    suite_id = _valid_dev_suite(_case("k1"))
    original = crud._fetch_or_raise

    def stale_fetch(session: Session, context: Any, project_id: Any, sid: Any) -> Any:
        # The row is now in this session's identity map with reviewer-1; a
        # Core UPDATE (what another transaction's commit looks like to this
        # one) changes the stored reviewer without touching that object.
        row = original(session, context, project_id, sid)
        session.execute(
            EvaluationSuite.__table__.update()
            .where(EvaluationSuite.__table__.c.id == sid)
            .values(reviewer_id="creator-1")
        )
        return row

    crud._fetch_or_raise = stale_fetch
    try:
        response = _lock(suite_id, _body(suite_id))
    finally:
        crud._fetch_or_raise = original
    check(
        "L1",
        "a reviewer change committed before the row lock is seen: the old reviewer is refused (403)",
        response.status == 403 and _state(suite_id) == ("draft", 0),
    )


def test_refusal_leaves_nothing_even_if_the_caller_commits() -> None:
    suite_id = _valid_dev_suite(_case("k1", expected_json={"answer": "tbd"}))
    context = types.SimpleNamespace(organization_id=_ORG)
    actor = crud.ActorInfo(actor_type="user", actor_id="reviewer-1", request_id="r")
    request = gate.LockRequest(
        gate.SamplePlan(("prompt injection",), 1, 0, 1), _current_sha(suite_id)
    )
    with Session(ENGINE) as session:
        try:
            gate.lock_validated_evaluation_suite(
                session,
                context,
                project_id=_PROJECT,
                suite_id=suite_id,
                actor=actor,
                member_lookup=lambda org, subject: True,
                lock_request=request,
            )
        except gate.SuiteLockValidationError as exc:
            check(
                "N2",
                "a direct call is refused with the typed finding",
                [f.code for f in exc.findings] == ["unresolved_expected_output"],
            )
        else:
            check("N2", "a direct call on an unresolved suite is refused", False)
        session.commit()
    check(
        "N2",
        "after a refused lock that the caller still commits, the suite is draft with no plan row",
        _state(suite_id) == ("draft", 0),
    )


def test_only_the_gate_calls_the_ungated_transition() -> None:
    offenders = []
    for path in (ROOT / "apps").rglob("*.py"):
        if "tests" in path.parts or path.name in (
            "evaluation_suite_crud.py",
            "evaluation_suite_lock_gate.py",
        ):
            continue
        if "lock_evaluation_suite(" in path.read_text(encoding="utf-8", errors="replace"):
            offenders.append(str(path.relative_to(ROOT)))
    check(
        "L1",
        f"no production module calls FTF-0601's ungated lock_evaluation_suite ({offenders})",
        offenders == [],
    )
    gate_src = (ROOT / "apps/api/app/evaluation/evaluation_suite_lock_gate.py").read_text()
    check(
        "L1",
        "the gate calls it through the module, so a reloaded FTF-0601 module is the one used",
        "_crud.lock_evaluation_suite(" in gate_src,
    )


# -- M1: migration shape ----------------------------------------------------------------


def test_m1_migration_shape() -> None:
    src = MIGRATION_PATH.read_text()
    check(
        "M1", "revision id fits alembic_version (<= 32)", len("0039_eval_suite_sample_plans") <= 32
    )
    check(
        "M1",
        "chains onto 0038_dataset_mapping_requests",
        'down_revision = "0038_dataset_mapping_requests"' in src,
    )
    downs: dict[str, str] = {}
    revisions: set[str] = set()
    for path in VERSIONS_DIR.glob("*.py"):
        text = path.read_text()
        rev = re.search(r'^revision = "([^"]+)"', text, re.M)
        down = re.search(r'^down_revision = "([^"]+)"', text, re.M)
        if rev:
            revisions.add(rev.group(1))
        if rev and down:
            downs[rev.group(1)] = down.group(1)
    heads = revisions - set(downs.values())
    check(
        "M1",
        f"exactly one migration head, 0039 (got {sorted(heads)})",
        heads == {"0039_eval_suite_sample_plans"},
    )
    check(
        "M1",
        "ENABLE and FORCE row level security",
        'op.execute("ALTER TABLE evaluation_suite_sample_plans ENABLE ROW LEVEL SECURITY")' in src
        and 'op.execute("ALTER TABLE evaluation_suite_sample_plans FORCE ROW LEVEL SECURITY")'
        in src,
    )
    check(
        "M1",
        "tenant policy on USING and WITH CHECK, never allow-all",
        "CREATE POLICY tenant_isolation ON evaluation_suite_sample_plans FOR ALL" in src
        and "USING (organization_id = {tenant_setting})" in src
        and "WITH CHECK (organization_id = {tenant_setting})" in src
        and "current_setting('app.current_organization_id', true)" in src
        and "USING (true)" not in src,
    )
    check(
        "M1",
        "sovra_app gets SELECT, INSERT only (no UPDATE, DELETE or ALL)",
        'GRANT SELECT, INSERT ON evaluation_suite_sample_plans TO "{APP_ROLE_NAME}"' in src
        and re.search(r"GRANT[^\n]*(UPDATE|DELETE|ALL)", src) is None,
    )
    for needle in (
        "uq_evaluation_suite_sample_plans_suite_id",
        "jsonb_array_length(required_categories) >= 1",
        "suite_sha256 ~ '^[0-9a-f]{64}$'",
        "min_independent_tasks BETWEEN 1 AND",
        '"evaluation_suites.organization_id",',
        "fk_evaluation_suite_sample_plans_org_project_suite",
        "fk_evaluation_suite_sample_plans_organization_id_project_id",
    ):
        check("M1", f"migration contains {needle}", needle in src)
    columns = set(re.findall(r'sa\.Column\(\s*"([a-z_0-9]+)"', src))
    orm = {c.name for c in gate.EvaluationSuiteSamplePlan.__table__.columns}
    check(
        "M1",
        f"the ORM model mirrors the migration's columns ({sorted(columns ^ orm)})",
        columns == orm,
    )


# -- K1: contracts -----------------------------------------------------------------------


def test_k1_contracts() -> None:
    spec = yaml.safe_load(INTERFACE_PATH.read_text())
    path = "/api/v1/organizations/{organization_id}/projects/{project_id}/evaluation-suites/{suite_id}/lock"
    post = spec["paths"][path]["post"]
    body_ref = (
        post.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
        .get("$ref")
    )
    check(
        "K1",
        "the lock operation declares its request body",
        body_ref == "#/components/schemas/EvaluationSuiteLockRequest",
    )
    conflict = (
        post["responses"]["409"].get("content", {}).get("application/json", {}).get("schema", {})
    )
    check(
        "K1",
        "the lock 409 declares the typed refusal",
        conflict.get("$ref") == "#/components/schemas/EvaluationSuiteLockRefusal",
    )
    schemas = spec["components"]["schemas"]
    finding = schemas.get("EvaluationSuiteLockFinding", {})
    check(
        "K1",
        "the finding code enum is exactly the gate's codes",
        set(finding.get("properties", {}).get("code", {}).get("enum", []))
        == set(gate.FINDING_CODES),
    )
    suite = schemas["EvaluationSuite"]
    check(
        "K1",
        "EvaluationSuite gains suite_sha256 additively (property, not required)",
        "suite_sha256" in suite["properties"] and "suite_sha256" not in suite["required"],
    )
    plan = schemas.get("EvaluationSuiteSamplePlan", {})
    check(
        "K1",
        "the plan schema bounds categories at 200 and counts at 1,000,000",
        plan.get("properties", {}).get("required_categories", {}).get("maxItems")
        == gate.MAX_PLAN_CATEGORIES
        and plan.get("properties", {}).get("min_independent_tasks", {}).get("maximum")
        == gate.MAX_PLAN_COUNT,
    )
    domain = json.loads(DOMAIN_PATH.read_text())
    check(
        "K1",
        "06-domain.schema.json declares the sample plan",
        "EvaluationSuiteSamplePlan" in domain.get("$defs", {}),
    )


def main() -> None:
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                try:
                    fn()
                except Exception as exc:  # noqa: BLE001 - a crash is a failed test, reported by name
                    FAILURES.append(f"FAIL [{CID}] {name} raised {type(exc).__name__}: {exc}")
    finally:
        _stop_server()

    if FAILURES:
        print(f"FTF-0603-F1 suite lock gate tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0603-F1 suite lock gate tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
