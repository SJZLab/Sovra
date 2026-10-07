#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg,index,union-attr"
# ruff: noqa: E501
"""FTF-0602-P1 -- EvaluationCase persistence + create/import/list/edit API
(no-DB unit/fault tests).

Runnable without Postgres or network egress: the REAL routes run on a real
uvicorn server over the REAL store, against SQLite built from the real ORM
tables (only the Postgres tenant guard and the audit writer are replaced). The
live-database evidence (row-level security, grants, composite foreign keys,
the CHECK constraints, the migration round trip, cross-tenant denial on raw SQL)
is `tests/integration/test_ftf_0602_p1_evaluation_cases.py`, run in CI's
`evaluation-case-integration` job.

Criteria (QUEUE Backlog, FTF-0602-P1):
  M1  migration 0023 shape: ENABLE+FORCE RLS, non-allow-all tenant policy,
      SELECT/INSERT/UPDATE and never DELETE, composite FKs, the closed
      severity / 13-grader vocabularies, the critical-oracle CHECK, weight,
      JSON object / size / non-empty-expected CHECKs, UQ (suite, case_key).
  X1  cross-tenant / wrong-project suite and case -> 404 `not_found`.
  C1  a critical case on exact_match / contains_fact / llm_judge (and every
      other non-independent grader) -> 400 `critical_oracle_not_independent`.
  F1  an ml_engineer / approver / viewer reading a final suite -> 403
      `final_suite_content_restricted` with no content; evaluator and both
      admins read it; a development suite is readable by every role.
  L1  a locked suite -> 409 `suite_locked` for import and edit.
  B1  one bad row inserts nothing (all-or-nothing); duplicate case_key in the
      batch or against the suite -> 409 `duplicate_case_key`, nothing written.
  N1  501 rows / 0 rows / a non-list -> 400; 500 rows are accepted.
  V1  field validation: closed vocabularies, expected != {}, 2 MiB, NUL,
      lone surrogates, depth, weight, strict types; errors never echo values.
  P1  list pagination by case_key, `limit` 1..500, `after` cursor.
  E1  edit: case_key immutable, merged row re-checked, audit names fields only.
  A1  audit `evaluation_case.import` (ids, count) / `evaluation_case.update`.
  R1  roles: FTF-0601's read/write sets, unchanged; body tenant cannot select.
  K1  06-interface.yaml declares the four operations; the router is mounted.

Run directly: python3 tests/tasks/test_ftf_0602_p1.py
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
import yaml
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.evaluation import evaluation_case_routes as routes  # noqa: E402
from app.evaluation import evaluation_case_store as store  # noqa: E402
from app.evaluation.evaluation_suite_routes import (  # noqa: E402
    EVALUATION_SUITE_READ_ROLES,
    EVALUATION_SUITE_WRITE_ROLES,
)
from app.evaluation.evaluation_suite_schema import EvaluationSuite  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0602-P1"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


MIGRATION_PATH = ROOT / "apps" / "api" / "alembic" / "versions" / "0023_evaluation_cases.py"
INTERFACE_PATH = ROOT / "packages" / "contracts" / "openapi" / "06-interface.yaml"
STORE_PATH = ROOT / "apps" / "api" / "app" / "evaluation" / "evaluation_case_store.py"

# -- M1: migration shape -------------------------------------------------------


def _migration() -> str:
    return MIGRATION_PATH.read_text()


def test_m1_migration_rls_grants_and_policy() -> None:
    src = _migration()
    check(
        "M1",
        "revision chains onto 0022_model_catalog_entries",
        'down_revision = "0022_model_catalog_entries"' in src,
    )
    check("M1", "revision id fits alembic_version (<= 32)", len("0023_evaluation_cases") <= 32)
    check(
        "M1",
        "ENABLE and FORCE row level security on evaluation_cases",
        'op.execute("ALTER TABLE evaluation_cases ENABLE ROW LEVEL SECURITY")' in src
        and 'op.execute("ALTER TABLE evaluation_cases FORCE ROW LEVEL SECURITY")' in src,
    )
    policy = re.search(
        r"CREATE POLICY tenant_isolation ON evaluation_cases FOR ALL[^\n]*\n[^\n]*\n[^\n]*", src
    )
    check(
        "M1",
        "tenant policy is scoped to app.current_organization_id on USING and WITH CHECK",
        policy is not None
        and "USING (organization_id = {tenant_setting})" in policy.group(0)
        and "WITH CHECK (organization_id = {tenant_setting})" in policy.group(0)
        and "current_setting('app.current_organization_id', true)" in src
        and "USING (true)" not in src
        and "WITH CHECK (true)" not in src,
    )
    check(
        "M1",
        "SELECT, INSERT, UPDATE granted",
        "GRANT SELECT, INSERT, UPDATE ON evaluation_cases" in src,
    )
    check(
        "M1",
        "no DELETE grant anywhere",
        re.search(r"GRANT[^\n]*DELETE", src) is None and "GRANT ALL" not in src,
    )


def test_m1_migration_constraints() -> None:
    src = _migration()
    for name in (
        "uq_evaluation_cases_suite_id_case_key",
        "ck_evaluation_cases_severity",
        "ck_evaluation_cases_grader_type",
        "ck_evaluation_cases_critical_independent_grader",
        "ck_evaluation_cases_weight_positive",
        "ck_evaluation_cases_payloads_are_objects",
        "ck_evaluation_cases_expected_not_empty",
        "ck_evaluation_cases_payload_size",
        "ck_evaluation_cases_case_key_not_blank",
        "fk_evaluation_cases_organization_id_project_id_suite_id",
        "uq_evaluation_suites_org_project_id_id",
    ):
        check("M1", f"migration declares {name}", f'"{name}"' in src)
    check(
        "M1",
        "composite FK to the suite inside organization and project",
        '["organization_id", "project_id", "suite_id"]' in src
        and '"evaluation_suites.organization_id"' in src
        and '"evaluation_suites.project_id"' in src
        and '"evaluation_suites.id"' in src,
    )
    check(
        "M1",
        "composite FK to projects inside the organization",
        '["organization_id", "project_id"]' in src
        and '"projects.organization_id", "projects.id"' in src,
    )
    check("M1", "RESTRICT on both composite FKs", src.count('ondelete="RESTRICT"') == 2)
    check("M1", "the migration owns the 2 MiB bound", "_MAX_JSON_BYTES = 2 * 1024 * 1024" in src)
    check(
        "M1",
        "grader vocabulary equals the store's 13 values, in order",
        re.findall(r'^    "(\w+)",$', src.split("_GRADER_TYPES = (")[1].split(")")[0], re.M)
        == list(store.GRADER_TYPES)
        and len(store.GRADER_TYPES) == 13,
    )
    check(
        "M1",
        "critical-capable graders in the migration equal the store's set",
        set(re.findall(r'"(\w+)"', src.split("_CRITICAL_GRADER_TYPES = (")[1].split(")")[0]))
        == set(store.CRITICAL_GRADER_TYPES),
    )
    check(
        "M1",
        "severity vocabulary equals the store's",
        re.findall(r'"(\w+)"', src.split("_SEVERITIES = (")[1].split(")")[0])
        == list(store.SEVERITIES),
    )


def _run_migration_with(mutated: str) -> bool:
    original = MIGRATION_PATH.read_text()
    MIGRATION_PATH.write_text(mutated)
    try:
        before = len(FAILURES)
        test_m1_migration_rls_grants_and_policy()
        test_m1_migration_constraints()
        caught = len(FAILURES) > before
        del FAILURES[before:]
        return caught
    finally:
        MIGRATION_PATH.write_text(original)


def test_m1_fault_injection_against_the_file_on_disk() -> None:
    src = MIGRATION_PATH.read_text()
    faults = {
        "dropping FORCE row level security": src.replace(
            'op.execute("ALTER TABLE evaluation_cases FORCE ROW LEVEL SECURITY")',
            'op.execute("SELECT 1")',
        ),
        "granting DELETE": src.replace(
            "GRANT SELECT, INSERT, UPDATE ON evaluation_cases",
            "GRANT SELECT, INSERT, UPDATE, DELETE ON evaluation_cases",
        ),
        "an allow-all tenant policy": src.replace(
            "USING (organization_id = {tenant_setting})", "USING (true)"
        ),
        "dropping the critical-oracle CHECK": src.replace(
            "ck_evaluation_cases_critical_independent_grader", "ck_dropped"
        ),
        "letting llm_judge be critical-capable": src.replace(
            '    "required_refusal",\n)\n_MAX_JSON_BYTES',
            '    "required_refusal",\n    "llm_judge",\n)\n_MAX_JSON_BYTES',
        ),
        "dropping the not-empty expected CHECK": src.replace(
            "ck_evaluation_cases_expected_not_empty", "ck_dropped2"
        ),
        "doubling the size bound": src.replace(
            "_MAX_JSON_BYTES = 2 * 1024 * 1024", "_MAX_JSON_BYTES = 4 * 1024 * 1024"
        ),
        "dropping the case_key unique key": src.replace(
            "uq_evaluation_cases_suite_id_case_key", "uq_dropped3"
        ),
    }
    for label, mutated in faults.items():
        check("M1", f"{label} is caught", mutated != src and _run_migration_with(mutated))


# -- fixtures: real store + real routes on SQLite -------------------------------

_ISSUER = "https://issuer.example"
_ORG = uuid.uuid4()
_OTHER_ORG = uuid.uuid4()
_PROJECT = uuid.uuid4()
_OTHER_PROJECT = uuid.uuid4()
_SUBJECT_ROLES = {
    "evaluator-1": Role.EVALUATOR,
    "wsadmin-1": Role.WORKSPACE_ADMIN,
    "orgadmin-1": Role.ORGANIZATION_ADMIN,
    "ml-1": Role.ML_ENGINEER,
    "approver-1": Role.APPROVER,
    "viewer-1": Role.VIEWER,
}
_PRINCIPALS = {
    key: AuthenticatedPrincipal(issuer=_ISSUER, subject=f"{key}-1")
    for key in ("evaluator", "wsadmin", "orgadmin", "ml", "approver", "viewer", "stranger")
}


def _principal_resolver(request: Any) -> AuthenticatedPrincipal | None:
    return _PRINCIPALS.get(request.headers.get("x-test-principal", ""))


def _role_lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
    return _SUBJECT_ROLES.get(subject) if organization_id == _ORG else None


ENGINE = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
EvaluationSuite.__table__.create(ENGINE)
store.EvaluationCaseRecord.__table__.create(ENGINE)
AUDIT: list[dict[str, Any]] = []


def _write_audit(session: Session, **kwargs: Any) -> None:
    session.info.setdefault("audit", []).append(kwargs)


def _passthrough_execute(session: Session, organization_id: uuid.UUID, statement: Any) -> Any:
    return session.execute(statement)


@contextlib.contextmanager
def _scoped_session(session_factory: Any, tenant_context: Any):
    """Commit on success, roll back on any exception -- like the real
    `tenant_scoped_session_for`; audit rows land only on commit."""
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


store.execute_tenant_scoped = _passthrough_execute
store.write_audit_event = _write_audit
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
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
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


def _request(
    base: str,
    method: str,
    path: str,
    *,
    principal: str | None = "evaluator",
    payload: Any = None,
    raw: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> _Http:
    hdrs: dict[str, str] = dict(headers or {})
    if principal is not None:
        hdrs["X-Test-Principal"] = principal
    body: bytes | None = raw
    if raw is None and payload is not None:
        body = json.dumps(payload).encode("utf-8")
    if body is not None:
        hdrs.setdefault("Content-Type", "application/json")
    parsed = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=20)
    try:
        conn.request(method, path, body=body, headers=hdrs)
        response = conn.getresponse()
        return _Http(response.status, response.read())
    finally:
        conn.close()


_SERVER: dict[str, Any] = {}


def _base() -> str:
    if "base" not in _SERVER:
        app = FastAPI()
        app.include_router(
            routes.build_evaluation_case_router(
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


_NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
_VERSION = [0]


def _seed_suite(
    *,
    org: uuid.UUID = _ORG,
    project: uuid.UUID = _PROJECT,
    purpose: str = "development",
    status: str = "draft",
) -> uuid.UUID:
    _VERSION[0] += 1
    kwargs: dict[str, Any] = {}
    if status == "locked":
        kwargs.update(reviewer_id="reviewer-1", locked_at=_NOW)
    suite = EvaluationSuite(
        id=uuid.uuid4(),
        organization_id=org,
        project_id=project,
        agent_manifest_id=uuid.uuid4(),
        version=_VERSION[0],
        status=status,
        purpose=purpose,
        name="suite",
        grading_logic_version=1,
        created_by="creator-1",
        created_at=_NOW,
        **kwargs,
    )
    suite_id: uuid.UUID = suite.id
    with Session(ENGINE) as session:
        session.add(suite)
        session.commit()
    return suite_id


def _path(
    suite: uuid.UUID, suffix: str = "", *, org: uuid.UUID = _ORG, project: uuid.UUID = _PROJECT
) -> str:
    return f"/api/v1/organizations/{org}/projects/{project}/evaluation-suites/{suite}/cases{suffix}"


def _case(key: str = "c1", **overrides: Any) -> dict[str, Any]:
    case: dict[str, Any] = {
        "case_key": key,
        "category": "critical side-effect prevention",
        "severity": "high",
        "language": "en",
        "group_key": f"group-{key}",
        "input_json": {"messages": [{"role": "user", "content": "Refund order 123"}]},
        "expected_json": {"must_not_call": ["refund_order"]},
        "grader_type": "tool_name",
    }
    case.update(overrides)
    return case


def _rows(suite: uuid.UUID) -> list[store.EvaluationCaseRecord]:
    with Session(ENGINE) as session:
        return list(session.query(store.EvaluationCaseRecord).filter_by(suite_id=suite).all())


def _import(suite: uuid.UUID, cases: list[Any], *, principal: str = "evaluator") -> _Http:
    return _request(_base(), "POST", _path(suite), principal=principal, payload={"cases": cases})


def _errors(result: _Http) -> list[tuple[Any, Any, Any]]:
    return [(e["index"], e["field"], e["code"]) for e in result.json().get("errors", [])]


# -- B1 / N1: import, all-or-nothing, bounds -----------------------------------


def test_b1_import_creates_cases_with_ids_audit_and_201() -> None:
    suite = _seed_suite()
    before = len(AUDIT)
    result = _import(
        suite, [_case("c1"), _case("c2", critical=True, weight=2.5, grader_config_json={"k": 1})]
    )
    check("B1", "import returns 201", result.status == 201)
    items = result.json()["items"]
    check(
        "B1",
        "two items returned in submitted order",
        [i["case_key"] for i in items] == ["c1", "c2"],
    )
    check(
        "B1",
        "item carries every stored field",
        items[0]["suite_id"] == str(suite)
        and items[0]["project_id"] == str(_PROJECT)
        and items[0]["organization_id"] == str(_ORG)
        and items[0]["critical"] is False
        and items[0]["weight"] == 1.0
        and items[0]["grader_config_json"] == {}
        and items[0]["created_by"] == "evaluator-1"
        and items[0]["expected_json"] == {"must_not_call": ["refund_order"]}
        and items[1]["critical"] is True
        and items[1]["weight"] == 2.5
        and items[1]["grader_config_json"] == {"k": 1},
    )
    check("B1", "two rows persisted", len(_rows(suite)) == 2)
    events = AUDIT[before:]
    check(
        "A1",
        "one evaluation_case.import audit event with ids and count, no content",
        len(events) == 1
        and events[0]["action"] == "evaluation_case.import"
        and events[0]["resource_type"] == "evaluation_suite"
        and events[0]["resource_id"] == str(suite)
        and events[0]["organization_id"] == _ORG
        and events[0]["actor_id"] == "evaluator-1"
        and events[0]["details"]["count"] == 2
        and events[0]["details"]["case_ids"] == [i["id"] for i in items]
        and "refund" not in json.dumps(events[0], default=str),
    )


def test_b1_one_bad_row_inserts_nothing() -> None:
    suite = _seed_suite()
    before = len(AUDIT)
    result = _import(suite, [_case("ok1"), _case("bad", severity="urgent"), _case("ok2")])
    check("B1", "a batch with one bad row is 400", result.status == 400)
    check(
        "B1",
        "the error names row 1 and field severity",
        _errors(result) == [(1, "severity", "invalid_value")],
    )
    check("B1", "the valid rows around it were not inserted", len(_rows(suite)) == 0)
    check("B1", "no audit event for a refused batch", len(AUDIT) == before)


def test_b1_every_bad_row_is_reported() -> None:
    suite = _seed_suite()
    result = _import(suite, [_case("a", category=""), _case("b"), _case("c", weight=0)])
    check(
        "B1",
        "all invalid rows are reported together",
        _errors(result) == [(0, "category", "invalid_value"), (2, "weight", "invalid_value")],
    )


def test_b1_duplicate_case_key_in_batch_is_409_and_writes_nothing() -> None:
    suite = _seed_suite()
    result = _import(suite, [_case("dup"), _case("other"), _case("dup")])
    check(
        "B1",
        "duplicate inside the batch is 409",
        result.status == 409 and result.json() == {"detail": "duplicate_case_key"},
    )
    check("B1", "nothing inserted", len(_rows(suite)) == 0)


def test_b1_duplicate_case_key_against_the_suite_is_409_and_writes_nothing() -> None:
    suite = _seed_suite()
    check("B1", "first import succeeds", _import(suite, [_case("k1")]).status == 201)
    result = _import(suite, [_case("new"), _case("k1")])
    check(
        "B1",
        "existing key is 409",
        result.status == 409 and result.json() == {"detail": "duplicate_case_key"},
    )
    check(
        "B1",
        "the non-duplicate row of the refused batch was not inserted",
        sorted(r.case_key for r in _rows(suite)) == ["k1"],
    )
    other = _seed_suite()
    check(
        "B1",
        "the same key in a different suite is fine",
        _import(other, [_case("k1")]).status == 201,
    )


def test_b1_case_key_is_case_sensitive_and_exact() -> None:
    suite = _seed_suite()
    check(
        "B1",
        "Case and case are different keys",
        _import(suite, [_case("Case"), _case("case")]).status == 201,
    )


def test_n1_batch_size_bounds() -> None:
    suite = _seed_suite()
    r501 = _import(suite, [_case(f"k{i}") for i in range(501)])
    check(
        "N1",
        "501 rows -> 400",
        r501.status == 400 and _errors(r501) == [(None, "cases", "count_out_of_range")],
    )
    check("N1", "501 rows insert nothing", len(_rows(suite)) == 0)
    r0 = _import(suite, [])
    check(
        "N1",
        "0 rows -> 400",
        r0.status == 400 and _errors(r0) == [(None, "cases", "count_out_of_range")],
    )
    r500 = _import(suite, [_case(f"k{i:03d}") for i in range(500)])
    check("N1", "exactly 500 rows -> 201", r500.status == 201 and len(r500.json()["items"]) == 500)
    check("N1", "500 rows persisted", len(_rows(suite)) == 500)
    r1 = _import(_seed_suite(), [_case("only")])
    check("N1", "exactly 1 row -> 201", r1.status == 201)


def test_n1_request_shape_errors() -> None:
    suite = _seed_suite()
    base = _base()
    for label, kwargs, expected in (
        ("invalid JSON", {"raw": b"{not json"}, [(None, None, "invalid_json")]),
        ("NaN constant", {"raw": b'{"cases": [NaN]}'}, [(None, None, "invalid_json")]),
        (
            "Infinity in a payload",
            {"raw": b'{"cases": [{"weight": Infinity}]}'},
            [(None, None, "invalid_json")],
        ),
        ("a JSON array", {"raw": b"[]"}, [(None, None, "invalid_type")]),
        ("a missing cases key", {"payload": {}}, [(None, None, "unknown_field")]),
        (
            "an extra top-level key",
            {"payload": {"cases": [_case()], "x": 1}},
            [(None, None, "unknown_field")],
        ),
        ("cases not a list", {"payload": {"cases": "nope"}}, [(None, "cases", "invalid_type")]),
        ("cases as an object", {"payload": {"cases": {"a": 1}}}, [(None, "cases", "invalid_type")]),
        ("a row that is not an object", {"payload": {"cases": [1]}}, [(0, None, "invalid_type")]),
    ):
        result = _request(base, "POST", _path(suite), **kwargs)
        check(
            "N1",
            f"{label} -> 400 with a stable code",
            result.status == 400 and _errors(result) == expected,
        )
    deep = b'{"cases": [' + b"[" * 100000 + b"]" * 100000 + b"]}"
    result = _request(base, "POST", _path(suite), raw=deep)
    check("N1", "absurd JSON nesting is a 400, not a 500", result.status == 400)
    check("N1", "no request-shape failure inserted a row", len(_rows(suite)) == 0)


def test_n1_oversized_body_is_refused_before_it_is_parsed() -> None:
    suite = _seed_suite()
    base = _base()
    big = b'{"cases": [], "pad": "' + b"a" * (routes.MAX_BODY_BYTES + 10) + b'"}'
    result = _request(base, "POST", _path(suite), raw=big)
    check(
        "N1",
        "a body over the cap is 400 body_too_large",
        result.status == 400 and result.json()["detail"] == "body_too_large",
    )
    check("N1", "the cap is 32 MiB", routes.MAX_BODY_BYTES == 32 * 1024 * 1024)
    at_cap = b'{"cases": [], "pad": "' + b"a" * (routes.MAX_BODY_BYTES - 40) + b'"}'
    result = _request(base, "POST", _path(suite), raw=at_cap)
    check(
        "N1",
        "a body under the cap is parsed (and then refused for its shape, not its size)",
        result.status == 400 and result.json()["detail"] != "body_too_large",
    )


# -- V1: field validation ---------------------------------------------------------


def _single(**overrides: Any) -> _Http:
    return _import(_seed_suite(), [_case("v", **overrides)])


def test_v1_required_fields() -> None:
    for field in store._REQUIRED_FIELDS:
        case = _case("v")
        del case[field]
        result = _import(_seed_suite(), [case])
        check(
            "V1",
            f"missing {field} -> required",
            result.status == 400 and _errors(result) == [(0, field, "required")],
        )


def test_v1_closed_vocabularies() -> None:
    check(
        "V1",
        "13 grader types",
        len(store.GRADER_TYPES) == 13 and len(set(store.GRADER_TYPES)) == 13,
    )
    for grader in store.GRADER_TYPES:
        result = _single(grader_type=grader)
        check("V1", f"grader_type {grader} accepted when not critical", result.status == 201)
    for grader in ("llm-judge", "LLM_JUDGE", "exact", "", "tool_name ", None, 5, ["tool_name"]):
        result = _single(grader_type=grader)
        check(
            "V1",
            f"grader_type {grader!r} refused",
            result.status == 400 and _errors(result) == [(0, "grader_type", "invalid_value")],
        )
    for severity in store.SEVERITIES:
        check("V1", f"severity {severity} accepted", _single(severity=severity).status == 201)
    for severity in ("High", "urgent", "", None, 1):
        result = _single(severity=severity)
        check(
            "V1",
            f"severity {severity!r} refused",
            result.status == 400 and _errors(result) == [(0, "severity", "invalid_value")],
        )


def test_c1_critical_requires_an_independent_oracle() -> None:
    for grader in store.GRADER_TYPES:
        result = _single(critical=True, grader_type=grader)
        if grader in store.CRITICAL_GRADER_TYPES:
            check("C1", f"critical + {grader} accepted", result.status == 201)
        else:
            check(
                "C1",
                f"critical + {grader} -> 400 critical_oracle_not_independent",
                result.status == 400
                and _errors(result) == [(0, "grader_type", "critical_oracle_not_independent")],
            )
    check(
        "C1",
        "exactly the six independent graders may be critical",
        store.CRITICAL_GRADER_TYPES
        == {
            "tool_name",
            "tool_arguments",
            "tool_sequence",
            "state_transition",
            "forbidden_action",
            "required_refusal",
        },
    )
    result = _single(critical=False, grader_type="llm_judge")
    check("C1", "a non-critical llm_judge case is allowed", result.status == 201)
    suite = _seed_suite()
    result = _import(suite, [_case("ok"), _case("bad", critical=True, grader_type="exact_match")])
    check(
        "C1",
        "a refused critical case leaves the batch's other rows uninserted",
        result.status == 400 and len(_rows(suite)) == 0,
    )
    result = _single(critical="yes")
    check(
        "C1",
        "critical must be a real boolean",
        result.status == 400 and _errors(result) == [(0, "critical", "invalid_type")],
    )
    result = _single(critical=1, grader_type="exact_match")
    check(
        "C1",
        "critical=1 is not boolean true and is refused as a type",
        result.status == 400 and _errors(result) == [(0, "critical", "invalid_type")],
    )


def test_v1_text_fields() -> None:
    for field, limit in (
        ("case_key", 200),
        ("category", 100),
        ("language", 35),
        ("group_key", 200),
    ):
        check(
            "V1",
            f"{field} at the limit ({limit}) accepted",
            _single(**{field: "x" * limit}).status == 201,
        )
        over = _single(**{field: "x" * (limit + 1)})
        check(
            "V1",
            f"{field} one over the limit refused",
            over.status == 400 and _errors(over) == [(0, field, "too_long")],
        )
        for bad in ("", " ", " pad", "pad ", "a\x00b", "\ud800", 5, None, ["a"]):
            result = _single(**{field: bad})
            check(
                "V1",
                f"{field}={bad!r} refused",
                result.status == 400 and _errors(result)[0][:2] == (0, field),
            )
    check("V1", "interior whitespace is fine", _single(case_key="a b c").status == 201)
    check("V1", "unicode text is fine", _single(category="اختبار — ✓").status == 201)


def test_v1_json_payloads() -> None:
    for field in store._JSON_FIELDS:
        for bad in ([], "x", 5, None, True, [1]):
            result = _single(**{field: bad})
            check(
                "V1",
                f"{field}={bad!r} is not an object",
                result.status == 400 and _errors(result) == [(0, field, "invalid_type")],
            )
    result = _single(expected_json={})
    check(
        "V1",
        "expected_json {} -> empty_object",
        result.status == 400 and _errors(result) == [(0, "expected_json", "empty_object")],
    )
    check("V1", "input_json {} is allowed", _single(input_json={}).status == 201)
    check("V1", "grader_config_json {} is allowed", _single(grader_config_json={}).status == 201)
    check(
        "V1", "expected_json may hold falsy values", _single(expected_json={"a": 0}).status == 201
    )
    for field in store._JSON_FIELDS:
        for label, value in (
            ("NUL in a string", {"a": "x\x00y"}),
            ("NUL in a key", {"a\x00": 1}),
            ("a lone surrogate", {"a": "\ud800"}),
            ("a lone surrogate key", {"\udfff": 1}),
        ):
            result = _single(**{field: value})
            check(
                "V1",
                f"{field} with {label} -> invalid_value",
                result.status == 400 and _errors(result) == [(0, field, "invalid_value")],
            )
        nested: Any = {"a": 1}
        for _ in range(store.MAX_JSON_DEPTH):
            nested = {"a": nested}
        result = _single(**{field: nested})
        check(
            "V1",
            f"{field} nested past the depth bound -> invalid_value",
            result.status == 400 and _errors(result) == [(0, field, "invalid_value")],
        )
    ok_depth: Any = {"leaf": 1}
    for _ in range(store.MAX_JSON_DEPTH - 2):
        ok_depth = {"a": ok_depth}
    check(
        "V1",
        "nesting just inside the depth bound is accepted",
        _single(input_json=ok_depth).status == 201,
    )


def test_v1_payload_size_boundary_is_exactly_2_mib() -> None:
    def sized(total: int) -> dict[str, Any]:
        base = len(json.dumps({"p": ""}, separators=(", ", ": ")).encode())
        return {"p": "a" * (total - base)}

    check("V1", "the bound is 2 MiB", store.MAX_JSON_BYTES == 2 * 1024 * 1024)
    for field in store._JSON_FIELDS:
        at = sized(store.MAX_JSON_BYTES)
        over = sized(store.MAX_JSON_BYTES + 1)
        check("V1", f"{field} of exactly 2 MiB accepted", _single(**{field: at}).status == 201)
        result = _single(**{field: over})
        check(
            "V1",
            f"{field} one byte over 2 MiB -> too_large",
            result.status == 400 and _errors(result) == [(0, field, "too_large")],
        )
    multibyte = {"p": "é" * (store.MAX_JSON_BYTES // 2)}
    result = _single(input_json=multibyte)
    check(
        "V1",
        "the bound counts UTF-8 bytes, not characters",
        result.status == 400 and _errors(result) == [(0, "input_json", "too_large")],
    )
    check(
        "V1",
        "a payload that only fits when whitespace is ignored is refused (Postgres renders ', ' and ': ')",
        _single(input_json={"k": ["a"] * 480000}).status == 400,
    )


def test_v1_weight() -> None:
    for good in (1, 0.5, 1e-9, 7, 1e308, 2.5):
        check("V1", f"weight {good} accepted", _single(weight=good).status == 201)
    check("V1", "an omitted weight defaults to 1", _single().json()["items"][0]["weight"] == 1.0)
    for bad in (0, -1, -0.0, "1", None, True, False, [1], {"a": 1}, 1e999):
        result = _request(
            _base(),
            "POST",
            _path(_seed_suite()),
            raw=json.dumps({"cases": [_case("w", weight=bad)]})
            .replace("Infinity", "1e999")
            .encode(),
        )
        check(
            "V1",
            f"weight {bad!r} refused",
            result.status == 400 and _errors(result)[0][:2] == (0, "weight"),
        )
    stored = _single(weight=3).json()["items"][0]["weight"]
    check(
        "V1", "an integer weight is stored as a number", stored == 3 and isinstance(stored, float)
    )


def test_v1_unknown_fields_and_no_value_echo() -> None:
    result = _single(secret_answer="TOPSECRET-VALUE")
    check(
        "V1",
        "an unknown field is refused",
        result.status == 400 and _errors(result) == [(0, None, "unknown_field")],
    )
    check(
        "V1",
        "the error does not echo the unknown key or value",
        b"TOPSECRET" not in result.body and b"secret_answer" not in result.body,
    )
    result = _single(
        severity="TOPSECRET-SEV",
        category="x" * 101,
        expected_json={"answer": "TOPSECRET-ANSWER"},
        weight="TOPSECRET-W",
    )
    check("V1", "no error echoes any submitted value", b"TOPSECRET" not in result.body)
    for tenant_field in ("organization_id", "project_id", "suite_id", "id", "created_by"):
        result = _single(**{tenant_field: str(_OTHER_ORG)})
        check(
            "R1",
            f"a body {tenant_field} cannot select anything -> 400",
            result.status == 400 and _errors(result) == [(0, None, "unknown_field")],
        )
    result = _request(
        _base(),
        "POST",
        _path(_seed_suite()),
        payload={"organization_id": str(_OTHER_ORG), "cases": [_case()]},
    )
    check(
        "R1",
        "a body organization_id naming another tenant is refused (403 by the role gate)",
        result.status == 403,
    )


# -- X1: tenant and project isolation ------------------------------------------------


def test_x1_cross_tenant_and_wrong_project_are_404() -> None:
    mine = _seed_suite()
    foreign = _seed_suite(org=_OTHER_ORG)
    other_project = _seed_suite(project=_OTHER_PROJECT)
    check("X1", "seed: my suite takes an import", _import(mine, [_case("seed")]).status == 201)
    seeded_case = _rows(mine)[0].id
    for label, suite, project in (
        ("another tenant's suite", foreign, _PROJECT),
        ("a suite of another project", other_project, _PROJECT),
        ("my suite under another project id", mine, _OTHER_PROJECT),
        ("a random suite id", uuid.uuid4(), _PROJECT),
    ):
        path = _path(suite, project=project)
        before = len(_rows(suite)) if suite in (foreign, other_project, mine) else 0
        r_post = _request(_base(), "POST", path, payload={"cases": [_case("x")]})
        r_list = _request(_base(), "GET", path)
        r_get = _request(_base(), "GET", path + f"/{seeded_case}")
        r_patch = _request(_base(), "PATCH", path + f"/{seeded_case}", payload={"category": "x"})
        for verb, result in (
            ("POST", r_post),
            ("GET list", r_list),
            ("GET one", r_get),
            ("PATCH", r_patch),
        ):
            check(
                "X1",
                f"{verb} on {label} -> 404 not_found",
                result.status == 404 and result.json() == {"detail": "not_found"},
            )
        check(
            "X1",
            f"{label}: nothing was written",
            (len(_rows(suite)) if suite in (foreign, other_project, mine) else 0) == before,
        )
    check(
        "X1",
        "my suite still has exactly its one seeded case",
        [r.case_key for r in _rows(mine)] == ["seed"],
    )
    foreign_case_import = _request(_base(), "GET", _path(mine, f"/{uuid.uuid4()}"))
    check(
        "X1",
        "a case id that does not exist is 404",
        foreign_case_import.status == 404 and foreign_case_import.json() == {"detail": "not_found"},
    )
    # a case that exists in another suite of mine is not reachable through this one
    sibling = _seed_suite()
    check("X1", "seed sibling", _import(sibling, [_case("sib")]).status == 201)
    sibling_case = _rows(sibling)[0].id
    check(
        "X1",
        "a case of another suite is 404 through this suite",
        _request(_base(), "GET", _path(mine, f"/{sibling_case}")).status == 404,
    )
    check(
        "X1",
        "...and cannot be patched through this suite",
        _request(
            _base(), "PATCH", _path(mine, f"/{sibling_case}"), payload={"category": "hijack"}
        ).status
        == 404,
    )
    check("X1", "the sibling case is unchanged", _rows(sibling)[0].category != "hijack")
    stranger = _request(_base(), "GET", _path(mine), principal="stranger")
    check("X1", "a principal with no role in the organization is refused", stranger.status == 403)
    other_org_path = _request(_base(), "GET", _path(mine, org=_OTHER_ORG))
    check(
        "X1",
        "an organization in the path the caller does not belong to is refused",
        other_org_path.status == 403,
    )
    check(
        "X1",
        "no principal -> 401",
        _request(_base(), "GET", _path(mine), principal=None).status == 401,
    )


# -- F1: final-suite content is restricted ----------------------------------------------


def test_f1_final_suite_content_is_restricted() -> None:
    final = _seed_suite(purpose="final")
    dev = _seed_suite(purpose="development")
    check(
        "F1",
        "evaluator imports into a final suite",
        _import(final, [_case("secret", expected_json={"answer": "FINAL-ANSWER-42"})]).status
        == 201,
    )
    check(
        "F1",
        "evaluator imports into a development suite",
        _import(dev, [_case("d1", expected_json={"answer": "DEV-ANSWER"})]).status == 201,
    )
    final_case = _rows(final)[0].id
    dev_case = _rows(dev)[0].id
    for principal in ("ml", "approver", "viewer"):
        for label, result in (
            ("list", _request(_base(), "GET", _path(final), principal=principal)),
            ("get", _request(_base(), "GET", _path(final, f"/{final_case}"), principal=principal)),
        ):
            check(
                "F1",
                f"{principal} {label} of a final suite -> 403 final_suite_content_restricted",
                result.status == 403
                and result.json() == {"detail": "final_suite_content_restricted"},
            )
            check(
                "F1",
                f"{principal} {label}: the response carries no content",
                b"FINAL-ANSWER" not in result.body and b"secret" not in result.body,
            )
        check(
            "F1",
            f"{principal} can still read a development suite",
            _request(_base(), "GET", _path(dev), principal=principal).status == 200
            and _request(_base(), "GET", _path(dev, f"/{dev_case}"), principal=principal).status
            == 200,
        )
    for principal in ("evaluator", "wsadmin", "orgadmin"):
        listed = _request(_base(), "GET", _path(final), principal=principal)
        check(
            "F1",
            f"{principal} lists a final suite",
            listed.status == 200
            and listed.json()["items"][0]["expected_json"] == {"answer": "FINAL-ANSWER-42"},
        )
        check(
            "F1",
            f"{principal} gets a final case",
            _request(_base(), "GET", _path(final, f"/{final_case}"), principal=principal).status
            == 200,
        )
    for principal in ("ml", "approver", "viewer"):
        wrote = _request(
            _base(), "POST", _path(final), principal=principal, payload={"cases": [_case("z")]}
        )
        patched = _request(
            _base(),
            "PATCH",
            _path(final, f"/{final_case}"),
            principal=principal,
            payload={"category": "z"},
        )
        check(
            "F1",
            f"{principal} cannot write to a final suite",
            wrote.status == 403 and patched.status == 403,
        )
    check(
        "F1",
        "the final suite still holds only the evaluator's case",
        [r.case_key for r in _rows(final)] == ["secret"],
    )
    check(
        "F1",
        "store-level guard: a restricted role is refused even when called directly (not only by the route)",
        _store_refuses(final, Role.ML_ENGINEER)
        and not _store_refuses(final, Role.EVALUATOR)
        and not _store_refuses(dev, Role.VIEWER),
    )
    check(
        "F1",
        "the final-content role set is exactly evaluator, workspace_admin, organization_admin",
        store.FINAL_SUITE_CONTENT_ROLES
        == {Role.EVALUATOR, Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN},
    )


def _store_refuses(suite: uuid.UUID, role: Role) -> bool:
    from types import SimpleNamespace

    ctx: Any = SimpleNamespace(organization_id=_ORG)
    with Session(ENGINE) as session:
        try:
            store.list_cases(
                session, ctx, project_id=_PROJECT, suite_id=suite, role=role, limit=10, after=None
            )
        except store.FinalSuiteContentRestrictedError:
            return True
        return False


# -- L1: locked suites ---------------------------------------------------------------------


def test_l1_locked_suite_refuses_import_and_edit_but_stays_readable() -> None:
    suite = _seed_suite()
    check("L1", "seed", _import(suite, [_case("keep")]).status == 201)
    case_id = _rows(suite)[0].id
    with Session(ENGINE) as session:
        row = session.get(EvaluationSuite, suite)
        row.status = "locked"
        row.reviewer_id = "reviewer-1"
        row.locked_at = _NOW
        session.commit()
    before = len(AUDIT)
    posted = _import(suite, [_case("late")])
    check(
        "L1",
        "import into a locked suite -> 409 suite_locked",
        posted.status == 409 and posted.json() == {"detail": "suite_locked"},
    )
    patched = _request(
        _base(), "PATCH", _path(suite, f"/{case_id}"), payload={"category": "late edit"}
    )
    check(
        "L1",
        "edit in a locked suite -> 409 suite_locked",
        patched.status == 409 and patched.json() == {"detail": "suite_locked"},
    )
    check(
        "L1",
        "nothing changed",
        [(r.case_key, r.category) for r in _rows(suite)]
        == [("keep", "critical side-effect prevention")],
    )
    check("L1", "no audit event for refused writes", len(AUDIT) == before)
    check(
        "L1",
        "a locked suite is still readable",
        _request(_base(), "GET", _path(suite)).status == 200
        and _request(_base(), "GET", _path(suite, f"/{case_id}")).status == 200,
    )
    locked_final = _seed_suite(purpose="final", status="locked")
    check(
        "L1",
        "a locked final suite also refuses writes",
        _import(locked_final, [_case("x")]).json() == {"detail": "suite_locked"},
    )
    check(
        "L1",
        "an invalid body on a locked suite is still just a 400 (validated before any lock)",
        _import(suite, [_case("x", severity="no")]).status == 400,
    )


# -- P1: pagination ---------------------------------------------------------------------------


def test_p1_cursor_pagination() -> None:
    suite = _seed_suite()
    keys = [f"k{i:03d}" for i in range(25)]
    check(
        "P1",
        "seed 25 cases (shuffled)",
        _import(suite, [_case(k) for k in reversed(keys)]).status == 201,
    )
    page = _request(_base(), "GET", _path(suite) + "?limit=10")
    body = page.json()
    check(
        "P1",
        "first page: 10 items ordered by case_key and a cursor",
        [i["case_key"] for i in body["items"]] == keys[:10] and body["next_after"] == "k009",
    )
    second = _request(_base(), "GET", _path(suite) + "?limit=10&after=k009").json()
    check(
        "P1",
        "second page continues strictly after the cursor",
        [i["case_key"] for i in second["items"]] == keys[10:20] and second["next_after"] == "k019",
    )
    third = _request(_base(), "GET", _path(suite) + "?limit=10&after=k019").json()
    check(
        "P1",
        "last page has the remainder and a null cursor",
        [i["case_key"] for i in third["items"]] == keys[20:] and third["next_after"] is None,
    )
    exact = _request(_base(), "GET", _path(suite) + "?limit=25").json()
    check(
        "P1",
        "a page that holds everything has a null cursor (no phantom next page)",
        len(exact["items"]) == 25 and exact["next_after"] is None,
    )
    one_short = _request(_base(), "GET", _path(suite) + "?limit=24").json()
    check(
        "P1",
        "limit one short of the total leaves a cursor",
        len(one_short["items"]) == 24 and one_short["next_after"] == "k023",
    )
    default = _request(_base(), "GET", _path(suite)).json()
    check(
        "P1",
        "no limit -> default page of up to 100",
        len(default["items"]) == 25 and default["next_after"] is None,
    )
    empty = _request(_base(), "GET", _path(_seed_suite())).json()
    check("P1", "an empty suite lists nothing", empty == {"items": [], "next_after": None})
    past = _request(_base(), "GET", _path(suite) + "?after=zzz").json()
    check(
        "P1",
        "a cursor past the end is an empty last page",
        past == {"items": [], "next_after": None},
    )
    seen: list[str] = []
    cursor: str | None = None
    while True:
        query = "?limit=7" + (f"&after={urllib.parse.quote(cursor)}" if cursor else "")
        chunk = _request(_base(), "GET", _path(suite) + query).json()
        seen.extend(i["case_key"] for i in chunk["items"])
        cursor = chunk["next_after"]
        if cursor is None:
            break
    check("P1", "walking the cursors yields every case exactly once, in order", seen == keys)
    big = _seed_suite()
    check("P1", "seed 500", _import(big, [_case(f"b{i:03d}") for i in range(500)]).status == 201)
    full = _request(_base(), "GET", _path(big) + "?limit=500").json()
    check(
        "P1", "limit=500 returns all 500", len(full["items"]) == 500 and full["next_after"] is None
    )
    check(
        "P1",
        "the default page size is 100",
        routes.DEFAULT_PAGE_SIZE == 100
        and len(_request(_base(), "GET", _path(big)).json()["items"]) == 100,
    )


def test_p1_query_validation() -> None:
    suite = _seed_suite()
    for query, expected in (
        ("?limit=0", [(None, "limit", "invalid_value")]),
        ("?limit=501", [(None, "limit", "invalid_value")]),
        ("?limit=-1", [(None, "limit", "invalid_value")]),
        ("?limit=abc", [(None, "limit", "invalid_value")]),
        ("?limit=", [(None, "limit", "invalid_value")]),
        ("?limit=1.5", [(None, "limit", "invalid_value")]),
        ("?limit=%D9%A3", [(None, "limit", "invalid_value")]),
        ("?after=", [(None, "after", "invalid_value")]),
        ("?after=" + "x" * 201, [(None, "after", "invalid_value")]),
        ("?after=a%00b", [(None, "after", "invalid_value")]),
    ):
        result = _request(_base(), "GET", _path(suite) + query)
        check(
            "P1",
            f"{query[:40]} -> 400 invalid_query",
            result.status == 400
            and result.json()["detail"] == "invalid_query"
            and _errors(result) == expected,
        )
    for ok in ("?limit=1", "?limit=500", "?after=" + "x" * 200):
        check(
            "P1",
            f"{ok[:30]} is accepted",
            _request(_base(), "GET", _path(suite) + ok).status == 200,
        )


# -- E1: edit ----------------------------------------------------------------------------------


def test_e1_edit_changes_named_fields_and_audits_names_only() -> None:
    suite = _seed_suite()
    check("E1", "seed", _import(suite, [_case("e1"), _case("e2")]).status == 201)
    row = {r.case_key: r for r in _rows(suite)}
    before = len(AUDIT)
    result = _request(
        _base(),
        "PATCH",
        _path(suite, f"/{row['e1'].id}"),
        payload={
            "category": "recovery",
            "severity": "low",
            "weight": 4,
            "expected_json": {"must_call": "SENSITIVE-VALUE"},
            "critical": True,
            "grader_type": "forbidden_action",
        },
    )
    body = result.json()
    check(
        "E1",
        "PATCH returns the updated case",
        result.status == 200
        and body["category"] == "recovery"
        and body["severity"] == "low"
        and body["weight"] == 4
        and body["expected_json"] == {"must_call": "SENSITIVE-VALUE"}
        and body["critical"] is True
        and body["grader_type"] == "forbidden_action"
        and body["case_key"] == "e1"
        and body["id"] == str(row["e1"].id),
    )
    untouched = {r.case_key: r for r in _rows(suite)}["e2"]
    check(
        "E1",
        "the other case is untouched",
        untouched.category == "critical side-effect prevention" and untouched.severity == "high",
    )
    check(
        "E1",
        "untouched fields of the edited case are preserved",
        body["language"] == "en"
        and body["group_key"] == "group-e1"
        and body["input_json"] == _case()["input_json"],
    )
    events = AUDIT[before:]
    check(
        "A1",
        "one evaluation_case.update audit event with sorted field names and no values",
        len(events) == 1
        and events[0]["action"] == "evaluation_case.update"
        and events[0]["resource_type"] == "evaluation_case"
        and events[0]["resource_id"] == str(row["e1"].id)
        and events[0]["actor_id"] == "evaluator-1"
        and events[0]["details"]
        == {
            "suite_id": str(suite),
            "fields": [
                "category",
                "critical",
                "expected_json",
                "grader_type",
                "severity",
                "weight",
            ],
        }
        and "SENSITIVE" not in json.dumps(events[0], default=str),
    )
    check("E1", "updated_at moved forward or stayed", body["updated_at"] >= body["created_at"])


def test_e1_case_key_is_immutable_and_patch_is_validated() -> None:
    suite = _seed_suite()
    check("E1", "seed", _import(suite, [_case("keep")]).status == 201)
    case_id = _rows(suite)[0].id
    path = _path(suite, f"/{case_id}")
    for label, payload, expected in (
        ("changing case_key", {"case_key": "renamed"}, [(None, "case_key", "immutable")]),
        ("re-sending the same case_key", {"case_key": "keep"}, [(None, "case_key", "immutable")]),
        ("an empty patch", {}, [(None, None, "empty_patch")]),
        ("an unknown field", {"nope": 1}, [(None, None, "unknown_field")]),
        (
            "a tenant field naming this organization",
            {"organization_id": str(_ORG)},
            [(None, None, "unknown_field")],
        ),
        (
            "an immutable server field",
            {"suite_id": str(uuid.uuid4())},
            [(None, None, "unknown_field")],
        ),
        ("a bad severity", {"severity": "x"}, [(None, "severity", "invalid_value")]),
        (
            "an empty expected_json",
            {"expected_json": {}},
            [(None, "expected_json", "empty_object")],
        ),
        ("a zero weight", {"weight": 0}, [(None, "weight", "invalid_value")]),
        ("a non-object body", [], [(None, None, "invalid_type")]),
    ):
        result = _request(_base(), "PATCH", path, payload=payload)
        check(
            "E1",
            f"{label} -> 400 {expected[0][2]}",
            result.status == 400 and _errors(result) == expected,
        )
    check(
        "E1",
        "nothing changed after the refused patches",
        [(r.case_key, r.category, r.severity) for r in _rows(suite)]
        == [("keep", "critical side-effect prevention", "high")],
    )
    bad_json = _request(_base(), "PATCH", path, raw=b"{oops")
    check(
        "E1",
        "an invalid JSON patch is 400",
        bad_json.status == 400 and _errors(bad_json) == [(None, None, "invalid_json")],
    )
    missing = _request(
        _base(), "PATCH", _path(suite, f"/{uuid.uuid4()}"), payload={"category": "x"}
    )
    check("E1", "patching a missing case -> 404", missing.status == 404)


def test_e1_merged_row_must_keep_an_independent_oracle() -> None:
    suite = _seed_suite()
    check(
        "E1",
        "seed critical + tool_name and non-critical + exact_match",
        _import(
            suite, [_case("crit", critical=True), _case("plain", grader_type="exact_match")]
        ).status
        == 201,
    )
    rows = {r.case_key: r for r in _rows(suite)}
    swap = _request(
        _base(), "PATCH", _path(suite, f"/{rows['crit'].id}"), payload={"grader_type": "llm_judge"}
    )
    check(
        "E1",
        "switching a critical case to llm_judge -> 400 critical_oracle_not_independent",
        swap.status == 400
        and _errors(swap) == [(None, "grader_type", "critical_oracle_not_independent")],
    )
    promote = _request(
        _base(), "PATCH", _path(suite, f"/{rows['plain'].id}"), payload={"critical": True}
    )
    check(
        "E1",
        "marking an exact_match case critical -> 400",
        promote.status == 400
        and _errors(promote) == [(None, "grader_type", "critical_oracle_not_independent")],
    )
    both = _request(
        _base(),
        "PATCH",
        _path(suite, f"/{rows['plain'].id}"),
        payload={"critical": True, "grader_type": "tool_sequence"},
    )
    check(
        "E1",
        "marking critical while switching to an independent grader works",
        both.status == 200 and both.json()["grader_type"] == "tool_sequence",
    )
    demote = _request(
        _base(),
        "PATCH",
        _path(suite, f"/{rows['crit'].id}"),
        payload={"critical": False, "grader_type": "llm_judge"},
    )
    check("E1", "un-marking critical while switching to llm_judge works", demote.status == 200)
    now = {r.case_key: r for r in _rows(suite)}
    check(
        "E1",
        "the refused patches changed nothing",
        now["crit"].grader_type == "llm_judge"
        and now["crit"].critical is False
        and now["plain"].critical is True,
    )


# -- R1: roles ---------------------------------------------------------------------------------------


def test_r1_roles_follow_ftf_0601_unchanged() -> None:
    check(
        "R1",
        "write roles are FTF-0601's set",
        EVALUATION_SUITE_WRITE_ROLES
        == {Role.EVALUATOR, Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN},
    )
    check("R1", "read roles are every role", EVALUATION_SUITE_READ_ROLES == set(Role))
    suite = _seed_suite()
    for principal in ("evaluator", "wsadmin", "orgadmin"):
        check(
            "R1",
            f"{principal} may import",
            _import(suite, [_case(f"w-{principal}")], principal=principal).status == 201,
        )
    for principal in ("ml", "approver", "viewer"):
        result = _import(suite, [_case(f"n-{principal}")], principal=principal)
        check(
            "R1",
            f"{principal} may not import (403 role_not_authorized)",
            result.status == 403 and result.json() == {"detail": "role_not_authorized"},
        )
    case_id = _rows(suite)[0].id
    for principal in ("ml", "approver", "viewer"):
        result = _request(
            _base(),
            "PATCH",
            _path(suite, f"/{case_id}"),
            principal=principal,
            payload={"category": "x"},
        )
        check("R1", f"{principal} may not edit", result.status == 403)
    check("R1", "no refused write inserted a row", len(_rows(suite)) == 3)
    for principal in ("evaluator", "wsadmin", "orgadmin", "ml", "approver", "viewer"):
        check(
            "R1",
            f"{principal} may read a development suite",
            _request(_base(), "GET", _path(suite), principal=principal).status == 200,
        )


# -- K1: contract and wiring ------------------------------------------------------------------------------


def test_k1_interface_declares_the_four_operations() -> None:
    spec = yaml.safe_load(INTERFACE_PATH.read_text())
    base = "/api/v1/organizations/{organization_id}/projects/{project_id}/evaluation-suites/{suite_id}/cases"
    paths = spec["paths"]
    check(
        "K1",
        "collection path declared with GET and POST",
        base in paths and set(paths[base]) >= {"get", "post"},
    )
    check(
        "K1",
        "item path declared with GET and PATCH",
        base + "/{case_id}" in paths and set(paths[base + "/{case_id}"]) >= {"get", "patch"},
    )
    check(
        "K1",
        "FTF-0601's four suite paths are still declared",
        all(
            p in paths
            for p in (
                "/api/v1/organizations/{organization_id}/projects/{project_id}/evaluation-suites",
                "/api/v1/organizations/{organization_id}/projects/{project_id}/evaluation-suites/{suite_id}",
                "/api/v1/organizations/{organization_id}/projects/{project_id}/evaluation-suites/{suite_id}/lock",
            )
        ),
    )
    check(
        "K1",
        "the 'does not declare a /cases route' sentence is gone",
        "does not declare a `/cases`" not in spec["info"]["description"]
        and "/cases" not in spec["info"]["description"].split("does not declare")[-1],
    )
    schemas = spec["components"]["schemas"]
    inp = schemas["EvaluationCaseInput"]
    check(
        "K1",
        "EvaluationCaseInput is closed with the required fields",
        inp["additionalProperties"] is False
        and set(inp["required"]) == set(store._REQUIRED_FIELDS),
    )
    check(
        "K1",
        "EvaluationCaseInput.properties are exactly the case fields",
        set(inp["properties"]) == set(store.CASE_FIELD_NAMES),
    )
    check(
        "K1",
        "grader enum equals the store's 13",
        schemas["EvaluationCaseGraderType"]["enum"] == list(store.GRADER_TYPES),
    )
    check(
        "K1",
        "severity enum equals the store's",
        schemas["EvaluationCaseSeverity"]["enum"] == list(store.SEVERITIES),
    )
    imp = schemas["EvaluationCaseImportRequest"]["properties"]["cases"]
    check(
        "K1",
        "import bounds are 1..500",
        imp["minItems"] == 1 and imp["maxItems"] == store.MAX_BATCH_SIZE == 500,
    )
    check(
        "K1",
        "patch has no case_key",
        "case_key" not in schemas["EvaluationCasePatchRequest"]["properties"],
    )
    check(
        "K1",
        "expected_json must be non-empty in the contract",
        inp["properties"]["expected_json"]["minProperties"] == 1,
    )
    codes = set(schemas["EvaluationCaseFieldError"]["properties"]["code"]["enum"])
    check(
        "K1",
        "the error-code enum contains critical_oracle_not_independent",
        "critical_oracle_not_independent" in codes,
    )
    emitted = set(re.findall(r'"([a-z_]+)"', STORE_PATH.read_text())) & {
        "required",
        "invalid_type",
        "invalid_value",
        "too_long",
        "too_large",
        "empty_object",
        "empty_patch",
        "unknown_field",
        "immutable",
        "critical_oracle_not_independent",
    }
    check(
        "K1",
        "every error code the store emits is in the contract",
        emitted <= codes and len(emitted) == 10,
    )
    statuses = {m: set(paths[base][m]["responses"]) for m in ("get", "post")}
    check(
        "K1",
        "POST documents 201/400/401/403/404/409",
        statuses["post"] >= {"201", "400", "401", "403", "404", "409"},
    )
    check(
        "K1",
        "GET documents 200/400/401/403/404",
        statuses["get"] >= {"200", "400", "401", "403", "404"},
    )


def test_k1_router_is_mounted_in_the_app_and_gated() -> None:
    main_src = (ROOT / "apps" / "api" / "app" / "main.py").read_text()
    check(
        "K1",
        "main.py imports and includes the case router",
        "build_evaluation_case_router" in main_src
        and main_src.count("build_evaluation_case_router") == 2,
    )
    env = {
        "SOVRA_ENVIRONMENT": "staging",
        "SOVRA_DATABASE_URL": "postgresql+psycopg://sovra_app:s3cr3t@db.internal:5432/sovra",
        "SOVRA_LITELLM_GATEWAY_URL": "https://litellm.internal.example",
        "SOVRA_PRIVACY_POLICY": "standard",
        "SOVRA_TLS_VERIFY": "true",
        "SOVRA_MAX_TRAINING_BUDGET_USD": "500.00",
        "SOVRA_LITELLM_API_KEY_SECRET_REF": "vault://sovra/litellm-api-key",
        "SOVRA_DATABASE_PASSWORD_SECRET_REF": "openbao://sovra/db-password",
        "SOVRA_OIDC_ISSUER": "http://idp.internal.example",
        "SOVRA_OIDC_CLIENT_ID": "sovra-factory",
        "SOVRA_OIDC_CLIENT_SECRET_REF": "vault://sovra/oidc-client-secret",
        "SOVRA_OIDC_REDIRECT_URI": "http://factory.internal.example/auth/callback",
        "SOVRA_OIDC_AUTHORIZATION_ENDPOINT": "http://idp.internal.example/authorize",
        "SOVRA_OIDC_TOKEN_ENDPOINT": "http://idp.internal.example/token",
        "SOVRA_OIDC_JWKS_URI": "http://idp.internal.example/jwks",
        "SOVRA_SESSION_SIGNING_KEY_SECRET_REF": "vault://sovra/session-signing-key",
        "SOVRA_OIDC_ALLOWED_ORIGIN": "http://factory.internal.example",
        "SOVRA_OIDC_CLIENT_SECRET_VALUE": "not-a-real-secret",
        "SOVRA_SESSION_SIGNING_KEY_VALUE": "not-a-real-signing-key",
    }
    added = set(env) - set(os.environ)
    old = {k: os.environ[k] for k in env if k in os.environ}
    os.environ.update(env)
    try:
        import app.main as main_module

        app = main_module.create_app()
    finally:
        for key in added:
            os.environ.pop(key, None)
        os.environ.update(old)
    with _serve(app) as base:
        for method, suffix in (
            ("GET", ""),
            ("POST", ""),
            ("GET", f"/{uuid.uuid4()}"),
            ("PATCH", f"/{uuid.uuid4()}"),
        ):
            result = _request(
                base,
                method,
                _path(uuid.uuid4(), suffix),
                principal=None,
                payload={} if method in ("POST", "PATCH") else None,
            )
            check(
                "K1",
                f"{method} .../cases{suffix[:2]} is mounted on the real app and fail-closed (401, not 404)",
                result.status == 401,
            )


def _code_without_docstrings(path: Path) -> str:
    import ast

    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(getattr(body[0], "value", None), ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body[0] = ast.Pass()
    return ast.unparse(tree)


def test_k1_store_does_not_compute_hashes_run_graders_or_parse_files() -> None:
    code = _code_without_docstrings(STORE_PATH) + _code_without_docstrings(
        ROOT / "apps" / "api" / "app" / "evaluation" / "evaluation_case_routes.py"
    )
    for needle in (
        "suite_sha256",
        "hashlib",
        "workers.evaluation",
        "csv",
        "UploadFile",
        "multipart",
        "yaml",
        "openpyxl",
    ):
        check("K1", f"the case store/routes never reference {needle}", needle not in code)


def test_k1_no_delete_path_exists() -> None:
    src = (
        STORE_PATH.read_text()
        + (ROOT / "apps" / "api" / "app" / "evaluation" / "evaluation_case_routes.py").read_text()
    )
    check(
        "K1",
        "no delete statement or route in the case store/routes",
        ".delete(" not in src
        and "delete(" not in src.replace("def ", "")
        and "@router.delete" not in src,
    )


# -- offline migration SQL ---------------------------------------------------------------------------------------


def _offline_sql(revision_range: str, action: str) -> str:
    env = dict(os.environ)
    env["SOVRA_DATABASE_URL"] = "postgresql+psycopg://x:y@localhost:5432/z"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", action, revision_range, "--sql"],
        cwd=str(ROOT / "apps" / "api"),
        capture_output=True,
        text=True,
        env=env,
    )
    check(CID, f"alembic {action} {revision_range} --sql exits 0", result.returncode == 0)
    return result.stdout


def test_x_migration_generates_the_expected_sql_offline() -> None:
    up = _offline_sql("0022_model_catalog_entries:0023_evaluation_cases", "upgrade")
    for needle in (
        "ALTER TABLE evaluation_suites ADD CONSTRAINT uq_evaluation_suites_org_project_id_id UNIQUE (organization_id, project_id, id)",
        "CREATE TABLE evaluation_cases",
        "ALTER TABLE evaluation_cases ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE evaluation_cases FORCE ROW LEVEL SECURITY",
        'GRANT SELECT, INSERT, UPDATE ON evaluation_cases TO "sovra_app"',
        "FOREIGN KEY(organization_id, project_id, suite_id) REFERENCES evaluation_suites (organization_id, project_id, id)",
        "FOREIGN KEY(organization_id, project_id) REFERENCES projects (organization_id, id)",
        "CHECK (NOT critical OR grader_type IN ('tool_name', 'tool_arguments', 'tool_sequence', 'state_transition', 'forbidden_action', 'required_refusal'))",
        "CHECK (expected_json <> '{}'::jsonb)",
        "CHECK (severity IN ('low', 'medium', 'high', 'critical'))",
        "octet_length(input_json::text) <= 2097152",
        "CHECK (weight > 0 AND weight < 'Infinity'::float8)",
        "UPDATE alembic_version SET version_num='0023_evaluation_cases'",
    ):
        check(CID, f"the upgrade SQL contains: {needle[:80]}", needle in up)
    check(
        CID,
        "exactly one tenant_isolation policy, never allow-all, and no DELETE",
        up.count("CREATE POLICY tenant_isolation ON") == 1
        and "USING (true)" not in up
        and "DELETE" not in up.replace("ON DELETE", ""),
    )
    down = _offline_sql("0023_evaluation_cases:0022_model_catalog_entries", "downgrade")
    for needle in (
        "DROP TABLE evaluation_cases",
        "DROP POLICY IF EXISTS tenant_isolation ON evaluation_cases",
        'REVOKE ALL ON evaluation_cases FROM "sovra_app"',
        "DROP CONSTRAINT uq_evaluation_suites_org_project_id_id",
        "UPDATE alembic_version SET version_num='0022_model_catalog_entries'",
    ):
        check(CID, f"the downgrade SQL contains: {needle[:80]}", needle in down)


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
        print(f"FTF-0602-P1 evaluation case tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0602-P1 evaluation case tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
