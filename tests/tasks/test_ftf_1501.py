#!/usr/bin/env python3
"""FTF-1501 -- Tenant isolation suite (no-DB unit/fault tests).

Runnable without Postgres or network egress. This suite proves, without
any database, the pure/structural parts of
`tests/security/tenant_isolation_suite.py` -- the live two-tenant
API/DB matrix itself is proven separately, against a real Postgres
cluster and a real uvicorn server, by that module's own `main()` (see
its own docstring for how to run it; `--stage task`'s own "applicable
real integration" requirement is satisfied there, not here):

1. `build_evidence` derives a correct `TenantIsolationEvidence` summary
   from real per-scenario results (counts, covered kinds, pass/fail
   split) and refuses to build evidence from zero results (Law 1 --
   an empty suite proves nothing).
2. `ScenarioResult`/`TenantIsolationEvidence` are content-free by
   construction: their own dataclass fields never carry a request/
   response body, only status codes and booleans (Section 47's own
   "no secrets/raw prompts in task reports").
3. negative_acceptance, "No security result from mocked auth or
   superuser-only DB tests": `assert_not_superuser_role` refuses (raises
   `TenantIsolationSuiteError`) when the connection's own `pg_roles.
   rolsuper` resolves true, and refuses (fail-closed, never silently
   passes) when it cannot resolve `current_user` at all -- proven here
   against a plain stub satisfying the same structural `.connect()`/
   `.execute(...).scalar()` shape `assert_not_superuser_role` actually
   calls, and does not raise for a genuine non-superuser answer.

Run directly: python3 tests/tasks/test_ftf_1501.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "security"))

import yaml  # noqa: E402

# Bare top-level import, not `tests.security.tenant_isolation_suite`: this
# repository's `tests/` tree has no `__init__.py` anywhere (a deliberate,
# established convention -- every other test file imports only from
# `app.*`/`scripts.*`, never from another test file), so a dotted package
# import here would make mypy see this one source file under two distinct
# module names ("tenant_isolation_suite" from its own direct scan, and
# "tests.security.tenant_isolation_suite" from a dotted import) and refuse
# to type-check the whole tree. This keeps both this file's own bare-module
# identity and the suite's consistent.
from tenant_isolation_suite import (  # noqa: E402
    ScenarioResult,
    TenantIsolationEvidence,
    TenantIsolationSuiteError,
    assert_not_superuser_role,
    build_evidence,
)

JSONSCHEMA_PATH = ROOT / "packages" / "contracts" / "jsonschema" / "15-domain.schema.json"
OPENAPI_PATH = ROOT / "packages" / "contracts" / "openapi" / "15-interface.yaml"

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _result(kind: str, check_name: str, passed: bool) -> ScenarioResult:
    return ScenarioResult(
        resource_kind=kind,
        check=check_name,
        method="GET",
        path=f"/api/v1/organizations/x/{kind}s/y",
        status=403 if passed else 200,
        expected_denied=True,
        passed=passed,
    )


def test_build_evidence_computes_real_counts() -> None:
    results = [
        _result("connection", "cross_tenant_api_read_denied", True),
        _result("connection", "unauthenticated_denied", True),
        _result("artifact", "cross_tenant_api_read_denied", True),
        _result("project", "cross_tenant_api_read_denied", False),
    ]
    evidence = build_evidence(results)
    check(
        "FTF-1501",
        "build_evidence counts scenarios_total from the real results list",
        evidence.scenarios_total == 4,
    )
    check(
        "FTF-1501",
        "build_evidence counts scenarios_passed from real passed=True results",
        evidence.scenarios_passed == 3,
    )
    check(
        "FTF-1501",
        "build_evidence derives scenarios_failed as total minus passed, never "
        "independently re-counted (cannot silently drift from the real results)",
        evidence.scenarios_failed == 1,
    )
    check(
        "FTF-1501",
        "build_evidence derives resource_kinds_covered from the real results, "
        "de-duplicated and order-preserving",
        evidence.resource_kinds_covered == ("connection", "artifact", "project"),
    )
    check(
        "FTF-1501",
        "build_evidence marks every real evidence record content_free",
        evidence.content_free is True,
    )
    check(
        "FTF-1501",
        "build_evidence assigns a real, non-empty suite_id and executed_at",
        bool(evidence.suite_id) and bool(evidence.executed_at),
    )


def test_build_evidence_refuses_empty_results() -> None:
    raised = False
    try:
        build_evidence([])
    except TenantIsolationSuiteError:
        raised = True
    check(
        "FTF-1501",
        "negative_acceptance/Law 1: build_evidence refuses to build evidence "
        "from zero scenario results -- an empty suite proves nothing",
        raised,
    )


def test_evidence_records_are_content_free_by_construction() -> None:
    scenario_fields = {f for f in ScenarioResult.__dataclass_fields__}
    evidence_fields = {f for f in TenantIsolationEvidence.__dataclass_fields__}
    forbidden = {"body", "payload", "secret", "secret_ref", "password", "token", "raw"}
    check(
        "FTF-1501",
        "ScenarioResult's own fields never include a request/response body "
        "or secret -- content-free by construction, not by convention",
        scenario_fields.isdisjoint(forbidden),
    )
    check(
        "FTF-1501",
        "TenantIsolationEvidence's own fields never include a request/response "
        "body or secret -- content-free by construction",
        evidence_fields.isdisjoint(forbidden),
    )
    result = _result("connection", "cross_tenant_api_read_denied", True)
    check(
        "FTF-1501",
        "ScenarioResult.to_dict() round-trips exactly the declared fields, " "nothing more",
        set(result.to_dict()) == scenario_fields,
    )


class _StubResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar(self) -> object:
        return self._value


class _StubConnection:
    def __init__(self, value: object) -> None:
        self._value = value

    def __enter__(self) -> _StubConnection:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, _statement: object) -> _StubResult:
        return _StubResult(self._value)


class _StubEngine:
    """Satisfies exactly the structural shape `assert_not_superuser_role`
    calls (`.connect()` -> context manager -> `.execute(...).scalar()`) --
    never a real `sqlalchemy.Engine`, proving this guard's own decision
    logic is unit-testable without a database."""

    def __init__(self, rolsuper_value: object) -> None:
        self._value = rolsuper_value

    def connect(self) -> _StubConnection:
        return _StubConnection(self._value)


def test_assert_not_superuser_role_refuses_a_real_superuser_answer() -> None:
    raised = False
    try:
        assert_not_superuser_role(_StubEngine(True))
    except TenantIsolationSuiteError:
        raised = True
    check(
        "FTF-1501",
        "negative_acceptance: assert_not_superuser_role refuses when "
        "pg_roles.rolsuper resolves true for the current connection -- RLS "
        "is not enforced against a superuser, so evidence collected through "
        "one would not be real tenant-isolation evidence",
        raised,
    )


def test_assert_not_superuser_role_refuses_an_unresolved_answer() -> None:
    raised = False
    try:
        assert_not_superuser_role(_StubEngine(None))
    except TenantIsolationSuiteError:
        raised = True
    check(
        "FTF-1501",
        "fail-closed: assert_not_superuser_role refuses (never silently "
        "passes) when it cannot resolve current_user against pg_roles at all",
        raised,
    )


def test_assert_not_superuser_role_accepts_a_real_non_superuser_answer() -> None:
    raised = False
    try:
        assert_not_superuser_role(_StubEngine(False))
    except TenantIsolationSuiteError:
        raised = True
    check(
        "FTF-1501",
        "assert_not_superuser_role does not raise for a genuine non-superuser "
        "connection -- this guard is a real precondition, not a permanent "
        "refusal, matching the live suite's own real sovra_app-role usage",
        not raised,
    )


def _load_jsonschema() -> dict:
    return json.loads(JSONSCHEMA_PATH.read_text(encoding="utf-8"))


def _load_openapi() -> dict:
    return yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))


def test_openapi_contract_has_no_placeholder_endpoints() -> None:
    doc = _load_openapi()
    check(
        "FTF-1501",
        "15-interface.yaml parses as valid OpenAPI YAML with an openapi version",
        isinstance(doc.get("openapi"), str) and doc["openapi"].startswith("3."),
    )
    check(
        "FTF-1501",
        "negative_acceptance / Law 11: 15-interface.yaml declares zero paths "
        "-- this task adds no new production HTTP route of its own",
        doc.get("paths") == {},
    )
    schemas = doc.get("components", {}).get("schemas", {})
    for entity in ("TenantIsolationScenarioResult", "TenantIsolationEvidence"):
        check(
            "FTF-1501",
            f"15-interface.yaml's components.schemas defines {entity}, "
            "matching the JSON Schema contract rather than diverging from it",
            entity in schemas and schemas[entity].get("type") == "object",
        )


def _validate_against_schema(instance: dict, schema: dict, defs: dict) -> list[str]:
    """A tiny, stdlib-only structural validator -- same approach and same
    reasoning as `tests/tasks/test_ftf_1601.py`'s own (adding the
    third-party `jsonschema` package would itself be a second, unapproved
    new-runtime-dependency ask-first item)."""
    errors: list[str] = []
    props = schema.get("properties", {})
    required = schema.get("required", [])
    for field in required:
        if field not in instance:
            errors.append(f"{field}: required but missing")
    for field, field_schema in props.items():
        if "$ref" in field_schema:
            ref_name = field_schema["$ref"].rsplit("/", 1)[-1]
            field_schema = defs[ref_name]
        if field not in instance:
            continue
        value = instance[field]
        expected_type = field_schema.get("type")
        if expected_type == "string" and not isinstance(value, str):
            errors.append(f"{field}: expected string, got {type(value).__name__}")
            continue
        if expected_type == "integer" and not isinstance(value, int):
            errors.append(f"{field}: expected integer, got {type(value).__name__}")
            continue
        if expected_type == "boolean" and not isinstance(value, bool):
            errors.append(f"{field}: expected boolean, got {type(value).__name__}")
            continue
        enum = field_schema.get("enum")
        if enum is not None and value not in enum:
            errors.append(f"{field}: {value!r} not in enum {enum}")
    return errors


def test_real_evidence_validates_against_the_frozen_contract() -> None:
    results = [_result("connection", "cross_tenant_api_read_denied", True)]
    evidence = build_evidence(results)
    schema_doc = _load_jsonschema()
    defs = schema_doc["$defs"]

    result_schema = defs["TenantIsolationScenarioResult"]
    errors = _validate_against_schema(results[0].to_dict(), result_schema, defs)
    check(
        "FTF-1501",
        f"a real ScenarioResult satisfies the frozen contract schema (errors: {errors})",
        errors == [],
    )

    errors = _validate_against_schema(evidence.to_dict(), defs["TenantIsolationEvidence"], defs)
    check(
        "FTF-1501",
        f"a real TenantIsolationEvidence satisfies the frozen contract schema (errors: {errors})",
        errors == [],
    )

    bad_instance = {**evidence.to_dict(), "content_free": False}
    errors = _validate_against_schema(bad_instance, defs["TenantIsolationEvidence"], defs)
    check(
        "FTF-1501",
        "the same validator genuinely rejects content_free=False against the "
        "frozen contract (not a decorative always-pass check)",
        errors != [],
    )


def main() -> None:
    test_build_evidence_computes_real_counts()
    test_build_evidence_refuses_empty_results()
    test_evidence_records_are_content_free_by_construction()
    test_assert_not_superuser_role_refuses_a_real_superuser_answer()
    test_assert_not_superuser_role_refuses_an_unresolved_answer()
    test_assert_not_superuser_role_accepts_a_real_non_superuser_answer()
    test_openapi_contract_has_no_placeholder_endpoints()
    test_real_evidence_validates_against_the_frozen_contract()

    if FAILURES:
        print(f"FTF-1501 tenant isolation suite (no-DB) tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1501 tenant isolation suite (no-DB) tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
