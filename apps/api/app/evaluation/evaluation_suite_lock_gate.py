"""Server-side suite lock validation (FTF-0603-F1, Section 23-30/26/69/77/81).

FTF-0603's negative acceptance -- "Missing critical categories, insufficient
sample plan and unresolved expected output block lock" -- used to be checked
only in the browser. A browser-only gate authorizes an effect by a UI control
alone, so this module is the enforcement: `lock_evaluation_suite`
(`evaluation_suite_crud.py`) calls `enforce_lock_gate` inside the same
tenant-scoped transaction that flips the suite to `locked`, after FTF-0601's
own reviewer/independence/membership/actor checks.

What a lock request carries (`POST .../evaluation-suites/{suite}/lock`):

  {"sample_plan": {"required_categories": [...],
                   "min_independent_tasks": n,
                   "min_critical_scenarios": n,
                   "min_cases_per_category": n},
   "suite_sha256": "<64 lowercase hex>"}

A malformed body is a 400 naming the field, never the value
(`LockRequestError`). A well-formed body that fails validation is a 409 with
one typed finding per violated rule (`SuiteLockValidationError`), and the
suite stays `draft`:

  sample_plan_missing            no plan was sent (insufficient sample plan)
  sample_plan_below_floor        a plan count is under its floor; subject is
                                 the plan field (insufficient sample plan)
  no_cases                       the suite has no case at all
  insufficient_independent_tasks fewer distinct group_key values than planned
  insufficient_critical_scenarios fewer critical groups than planned
  missing_critical_category      a required category has no critical case
  insufficient_category_sample   a required category has fewer groups than
                                 `min_cases_per_category`
  unresolved_expected_output     an expected output still holds a placeholder;
                                 subject is the case_key, never the value
  non_canonical_number           a case holds an integer outside the
                                 IEEE-754 safe range, so no hash both sides
                                 agree on exists; subject is the case_key
  suite_sha256_required          no reviewed hash was sent
  suite_sha256_mismatch          the reviewed hash is not the suite's current
                                 hash (the suite changed after review)

Floors (Section 69.3): every plan needs at least one required category, one
independent task and one case per required category. A `final` suite's plan
also needs `min_independent_tasks >= 1000` and `min_critical_scenarios >=
100` ("Default reference final suite contains >= 1,000 independent tasks and
>= 100 critical scenarios"). A plan may raise a floor, never lower it.

Independence (Section 69.2): "multiple turns from one conversation, repeated
seeds and template-derived near duplicates are not independent tasks" -- the
case contract's `group_key` names that cluster, so every count here counts
distinct `group_key` values, never rows.

`suite_sha256`: SHA-256 over the UTF-8 bytes of a canonical JSON document
(RFC 8785 style: object keys sorted by UTF-16 code units, ECMAScript number
and string serialization, no whitespace) holding the organization, project,
suite identity and every case in case_key order (UTF-16 code-unit order). The
browser computes the same bytes with `JSON.stringify` and `Array.sort`, so the
two sides agree byte for byte; the shared vector is
`packages/contracts/test-vectors/evaluation-suite-sha256.json`.

On success one `evaluation_suite_sample_plans` row (insert-only, migration
`0039_eval_suite_sample_plans`) records the plan, the observed counts and the
hash. `sovra_app` has SELECT/INSERT only on it, so a recorded plan never
changes.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.db.tenant_context import TenantContext
from app.evaluation.evaluation_case_store import CATEGORY_MAX_LENGTH, EvaluationCaseRecord
from app.evaluation.evaluation_suite_sample_plan_schema import EvaluationSuiteSamplePlan
from app.evaluation.evaluation_suite_schema import PURPOSE_FINAL, EvaluationSuite

__all__ = [
    "FINAL_MIN_CRITICAL_SCENARIOS",
    "FINDING_CODES",
    "FINAL_MIN_INDEPENDENT_TASKS",
    "HASH_FORMAT",
    "MAX_PLAN_CATEGORIES",
    "MAX_PLAN_COUNT",
    "EvaluationSuiteSamplePlan",
    "Finding",
    "LockRequest",
    "LockRequestError",
    "NonCanonicalValueError",
    "SamplePlan",
    "SuiteLockValidationError",
    "canonical_json",
    "compute_suite_sha256",
    "content_findings",
    "enforce_lock_gate",
    "has_unresolved_expected",
    "lock_suite_row",
    "parse_lock_request",
    "parse_sample_plan",
    "plan_floor_findings",
    "recorded_suite_sha256s",
]

FINAL_MIN_INDEPENDENT_TASKS = 1000
FINAL_MIN_CRITICAL_SCENARIOS = 100
MAX_PLAN_CATEGORIES = 200
MAX_PLAN_COUNT = 1_000_000
HASH_FORMAT = "sovra.evaluation_suite.sha256.v1"
# Largest integer an IEEE-754 double (the browser's only number type) holds
# exactly; past it the two sides would hash different digits.
MAX_SAFE_INTEGER = 2**53 - 1

# A placeholder in an expected output means the answer is not decided yet.
UNRESOLVED_MARKERS: frozenset[str] = frozenset(
    {"", "todo", "tbd", "tbc", "unresolved", "fixme", "?"}
)

# Every code a lock refusal can carry (the contract enum mirrors this tuple).
FINDING_CODES: tuple[str, ...] = (
    "sample_plan_missing",
    "sample_plan_below_floor",
    "no_cases",
    "insufficient_independent_tasks",
    "insufficient_critical_scenarios",
    "missing_critical_category",
    "insufficient_category_sample",
    "unresolved_expected_output",
    "non_canonical_number",
    "suite_sha256_required",
    "suite_sha256_mismatch",
)

_HEX_DIGITS = frozenset("0123456789abcdef")
_BODY_FIELDS = frozenset({"sample_plan", "suite_sha256"})
_COUNT_FIELDS = ("min_independent_tasks", "min_critical_scenarios", "min_cases_per_category")
_PLAN_FIELDS = frozenset({"required_categories", *_COUNT_FIELDS})


class LockRequestError(ValueError):
    """The lock body is malformed. Names the field, never the value."""

    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(f"invalid_lock_request: {field}")


class NonCanonicalValueError(ValueError):
    """A value has no canonical form both the API and the browser agree on."""


@dataclass(frozen=True)
class SamplePlan:
    required_categories: tuple[str, ...]
    min_independent_tasks: int
    min_critical_scenarios: int
    min_cases_per_category: int


@dataclass(frozen=True)
class LockRequest:
    sample_plan: SamplePlan | None
    suite_sha256: str | None


@dataclass(frozen=True)
class Finding:
    code: str
    subject: str | None = None
    need: int | None = None
    have: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "subject": self.subject, "need": self.need, "have": self.have}


class SuiteLockValidationError(Exception):
    """One or more lock rules failed; the suite was not changed."""

    def __init__(self, findings: Sequence[Finding]) -> None:
        self.findings = tuple(findings)
        super().__init__("suite_lock_validation_failed: " + ",".join(f.code for f in findings))


# -- request parsing (pure) -----------------------------------------------------


def _is_sha256_hex(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX_DIGITS


def _plan_count(raw: Mapping[str, Any], name: str) -> int:
    value = raw[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise LockRequestError(f"sample_plan.{name}")
    if value < 0 or value > MAX_PLAN_COUNT:
        raise LockRequestError(f"sample_plan.{name}")
    return value


def _plan_category(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LockRequestError("sample_plan.required_categories")
    if len(value) > CATEGORY_MAX_LENGTH:
        raise LockRequestError("sample_plan.required_categories")
    return value


def parse_sample_plan(raw: object) -> SamplePlan:
    """A plan has exactly the four fields. More than `MAX_PLAN_CATEGORIES`
    categories is refused outright -- never silently truncated."""
    if not isinstance(raw, dict) or set(raw) != _PLAN_FIELDS:
        raise LockRequestError("sample_plan")
    categories = raw["required_categories"]
    if not isinstance(categories, list) or len(categories) > MAX_PLAN_CATEGORIES:
        raise LockRequestError("sample_plan.required_categories")
    parsed = tuple(_plan_category(value) for value in categories)
    if len(set(parsed)) != len(parsed):
        raise LockRequestError("sample_plan.required_categories")
    return SamplePlan(
        required_categories=parsed,
        min_independent_tasks=_plan_count(raw, "min_independent_tasks"),
        min_critical_scenarios=_plan_count(raw, "min_critical_scenarios"),
        min_cases_per_category=_plan_count(raw, "min_cases_per_category"),
    )


def parse_lock_request(payload: Mapping[str, Any]) -> LockRequest:
    """Both fields are optional at the shape level: a missing plan or hash is
    a typed 409 finding (the clause it violates), not a 400."""
    if not set(payload) <= _BODY_FIELDS:
        raise LockRequestError("body")
    suite_sha256 = payload.get("suite_sha256")
    if suite_sha256 is not None and not _is_sha256_hex(suite_sha256):
        raise LockRequestError("suite_sha256")
    raw_plan = payload.get("sample_plan")
    plan = None if raw_plan is None else parse_sample_plan(raw_plan)
    return LockRequest(sample_plan=plan, suite_sha256=suite_sha256)


# -- validation (pure) ----------------------------------------------------------


def plan_floor_findings(plan: SamplePlan, purpose: str) -> list[Finding]:
    floors = {"min_independent_tasks": 1, "min_critical_scenarios": 0, "min_cases_per_category": 1}
    if purpose == PURPOSE_FINAL:
        floors["min_independent_tasks"] = FINAL_MIN_INDEPENDENT_TASKS
        floors["min_critical_scenarios"] = FINAL_MIN_CRITICAL_SCENARIOS
    findings: list[Finding] = []
    if not plan.required_categories:
        findings.append(Finding("sample_plan_below_floor", "required_categories", 1, 0))
    for name in _COUNT_FIELDS:
        have = getattr(plan, name)
        if have < floors[name]:
            findings.append(Finding("sample_plan_below_floor", name, floors[name], have))
    return findings


def has_unresolved_expected(value: object) -> bool:
    """True when any leaf is null or a placeholder string, or any object or
    array is empty."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in UNRESOLVED_MARKERS
    if isinstance(value, list):
        return not value or any(has_unresolved_expected(item) for item in value)
    if isinstance(value, dict):
        return not value or any(has_unresolved_expected(item) for item in value.values())
    return False


def _groups(cases: Sequence[EvaluationCaseRecord]) -> int:
    return len({case.group_key for case in cases})


def content_findings(
    cases: Sequence[EvaluationCaseRecord], plan: SamplePlan | None
) -> list[Finding]:
    """`cases` must already be in case_key order (findings follow it)."""
    if not cases:
        return [Finding("no_cases")]
    findings: list[Finding] = []
    if plan is not None:
        independent = _groups(cases)
        if independent < plan.min_independent_tasks:
            findings.append(
                Finding(
                    "insufficient_independent_tasks", None, plan.min_independent_tasks, independent
                )
            )
        critical = _groups([case for case in cases if case.critical])
        if critical < plan.min_critical_scenarios:
            findings.append(
                Finding(
                    "insufficient_critical_scenarios", None, plan.min_critical_scenarios, critical
                )
            )
        for category in plan.required_categories:
            in_category = [case for case in cases if case.category == category]
            if not any(case.critical for case in in_category):
                findings.append(Finding("missing_critical_category", category))
            sample = _groups(in_category)
            if sample < plan.min_cases_per_category:
                findings.append(
                    Finding(
                        "insufficient_category_sample",
                        category,
                        plan.min_cases_per_category,
                        sample,
                    )
                )
    for case in cases:
        if has_unresolved_expected(case.expected_json):
            findings.append(Finding("unresolved_expected_output", case.case_key))
    return findings


# -- canonical hash (pure) --------------------------------------------------------


def _utf16_order(text: str) -> bytes:
    return text.encode("utf-16-be", "surrogatepass")


def _es_number(value: float) -> str:
    """ECMAScript Number::toString for a finite double (what JSON.stringify
    writes); -0 is "0". `repr` gives the same shortest round-trip digits."""
    if not math.isfinite(value):
        raise NonCanonicalValueError("non-finite number")
    sign = "-" if value < 0 else ""
    parts = Decimal(repr(abs(value))).normalize().as_tuple()
    digits = "".join(str(d) for d in parts.digits)
    k = len(digits)
    n = k + int(parts.exponent)
    if n > 21 or n <= -6:
        mantissa = digits if k == 1 else digits[0] + "." + digits[1:]
        return f"{sign}{mantissa}e{n - 1:+d}"
    if n >= k:
        return sign + digits + "0" * (n - k)
    if n > 0:
        return sign + digits[:n] + "." + digits[n:]
    return sign + "0." + "0" * -n + digits


def _json_string(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def canonical_json(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise NonCanonicalValueError("integer outside the IEEE-754 safe range")
        return str(value)
    if isinstance(value, float):
        return _es_number(value)
    if isinstance(value, str):
        return _json_string(value)
    if isinstance(value, list):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        keys = sorted(value, key=_utf16_order)
        return "{" + ",".join(_json_string(k) + ":" + canonical_json(value[k]) for k in keys) + "}"
    raise NonCanonicalValueError(f"unsupported type {type(value).__name__}")


def _case_identity(case: Any) -> dict[str, object]:
    return {
        "case_key": case.case_key,
        "category": case.category,
        "severity": case.severity,
        "critical": case.critical,
        "language": case.language,
        "group_key": case.group_key,
        "input_json": case.input_json,
        "expected_json": case.expected_json,
        "grader_type": case.grader_type,
        "grader_config_json": case.grader_config_json,
        "weight": case.weight,
    }


def _ordered(cases: Sequence[Any]) -> list[Any]:
    return sorted(cases, key=lambda case: _utf16_order(case.case_key))


def compute_suite_sha256(suite: Any, cases: Sequence[Any]) -> str:
    """`suite` needs organization_id, project_id, id, agent_manifest_id,
    version, purpose and grading_logic_version; each case the eleven graded
    fields. Never server ids, names, reviewers or timestamps."""
    document = {
        "format": HASH_FORMAT,
        "organization_id": str(suite.organization_id),
        "project_id": str(suite.project_id),
        "suite": {
            "id": str(suite.id),
            "agent_manifest_id": str(suite.agent_manifest_id),
            "version": suite.version,
            "purpose": suite.purpose,
            "grading_logic_version": suite.grading_logic_version,
        },
        "cases": [_case_identity(case) for case in _ordered(cases)],
    }
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


# -- database ---------------------------------------------------------------------


def lock_suite_row(session: Session, context: TenantContext, suite: EvaluationSuite) -> None:
    """Takes the suite row FOR UPDATE -- the same row lock every case write
    takes (`evaluation_case_store`) -- and reloads `suite` from it, so no
    case or reviewer can change between validation and the lock."""
    statement = (
        select(EvaluationSuite)
        .where(EvaluationSuite.id == suite.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    execute_tenant_scoped(session, context.organization_id, statement).scalars().one()


def _load_cases(
    session: Session, context: TenantContext, suite: EvaluationSuite
) -> list[EvaluationCaseRecord]:
    statement = select(EvaluationCaseRecord).where(EvaluationCaseRecord.suite_id == suite.id)
    result = execute_tenant_scoped(session, context.organization_id, statement)
    return _ordered(list(result.scalars().all()))


def _hash_findings(
    suite: EvaluationSuite, cases: Sequence[EvaluationCaseRecord], reviewed: str | None
) -> tuple[list[Finding], str | None]:
    try:
        current = compute_suite_sha256(suite, cases)
    except NonCanonicalValueError:
        bad = [case.case_key for case in cases if not _canonical_case(case)]
        return [Finding("non_canonical_number", key) for key in bad], None
    if reviewed is None:
        return [Finding("suite_sha256_required")], current
    if reviewed != current:
        return [Finding("suite_sha256_mismatch")], current
    return [], current


def _canonical_case(case: EvaluationCaseRecord) -> bool:
    try:
        canonical_json(_case_identity(case))
    except NonCanonicalValueError:
        return False
    return True


def enforce_lock_gate(
    session: Session,
    context: TenantContext,
    suite: EvaluationSuite,
    request: LockRequest,
    *,
    actor_id: str,
) -> str:
    """Validates the suite against the plan and the reviewed hash and, only
    when every rule passes, records the plan. Returns `suite_sha256`. Raises
    `SuiteLockValidationError` (nothing written) otherwise. The caller has
    already run FTF-0601's checks and flips the status after this returns."""
    cases = _load_cases(session, context, suite)
    plan = request.sample_plan
    findings: list[Finding] = []
    if plan is None:
        findings.append(Finding("sample_plan_missing"))
    else:
        findings.extend(plan_floor_findings(plan, suite.purpose))
    findings.extend(content_findings(cases, plan))
    hash_findings, current = _hash_findings(suite, cases, request.suite_sha256)
    findings.extend(hash_findings)
    if findings or plan is None or current is None:
        raise SuiteLockValidationError(findings)
    session.add(
        EvaluationSuiteSamplePlan(
            id=uuid.uuid4(),
            organization_id=suite.organization_id,
            project_id=suite.project_id,
            suite_id=suite.id,
            required_categories=list(plan.required_categories),
            min_independent_tasks=plan.min_independent_tasks,
            min_critical_scenarios=plan.min_critical_scenarios,
            min_cases_per_category=plan.min_cases_per_category,
            case_count=len(cases),
            independent_task_count=_groups(cases),
            critical_scenario_count=_groups([case for case in cases if case.critical]),
            suite_sha256=current,
            created_by=actor_id,
            created_at=datetime.now(UTC),
        )
    )
    return current


def recorded_suite_sha256s(
    session: Session, context: TenantContext, suite_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """The hash each of these suites was locked at (absent for a draft)."""
    statement = select(
        EvaluationSuiteSamplePlan.suite_id, EvaluationSuiteSamplePlan.suite_sha256
    ).where(EvaluationSuiteSamplePlan.suite_id.in_(list(suite_ids)))
    result = execute_tenant_scoped(session, context.organization_id, statement)
    return {row[0]: row[1] for row in result.all()}
