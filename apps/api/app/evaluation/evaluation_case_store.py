"""EvaluationCase store (FTF-0602-P1, Section 6.14/23/24/26/27/39/65/69.1/69.2/77/78.2/81).

The persistence half FTF-0602's case editor/import UI needs. The table and its
grants live in `0023_evaluation_cases.py`. This module owns the validation
rules, the all-or-nothing batch insert, the cursor listing and the in-place
edit of a case, all inside the caller's tenant-scoped transaction.

What it enforces (each one also has a database CHECK or key as a backstop, so a
raw write cannot slip past the application layer):

- A case belongs to exactly one suite of the caller's own organization and
  project; a foreign or missing suite/case is the same `not_found`.
- Every write takes the suite row `FOR UPDATE` and refuses a `locked` suite
  (`SuiteLockedError`), so a case can never appear in, or change inside, a
  suite that was locked first (Section 69.1: final content is frozen).
- `critical` cases must use an oracle that is independent of the model: only
  `tool_name`, `tool_arguments`, `tool_sequence`, `state_transition`,
  `forbidden_action` and `required_refusal` qualify (Section 27 / 69.2).
  `exact_match`, `contains_fact` and `llm_judge` (and the rest) never do.
- `expected_json` is never `{}`; each JSON payload is an object of at most
  2 MiB, free of NUL characters and unencodable (lone-surrogate) text, and of
  bounded depth; `weight` is finite and strictly positive.
- A batch import is all-or-nothing: one bad row, a duplicate `case_key` inside
  the batch or against the suite inserts nothing.
- Final-purpose suite content (list, get, import, edit) is restricted to
  `evaluator`, `workspace_admin` and `organization_admin` (Section 69.1:
  "Neither API URL access, logs nor exception messages may disclose final
  answers to ordinary trainer roles"). The restriction is applied here, not
  only in the route, so a new caller cannot forget it.

Validation errors name the row, the field and a stable code; they never carry
the submitted value (a final suite's answers must not leak through an error).

Out of scope on purpose: computing `suite_sha256` (FTF-0603), running graders,
widening FTF-0601's roles, and parsing uploaded files on the server (the import
body is JSON rows the client already parsed).
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, String, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.auth.audit_event_writer import write_audit_event
from app.auth.organization_workspace_schema import Base
from app.auth.rbac_middleware import Role
from app.auth.tenant_isolation_query_guards import execute_tenant_scoped
from app.db.tenant_context import TenantContext
from app.evaluation.evaluation_suite_schema import PURPOSE_FINAL, EvaluationSuite

__all__ = [
    "CASE_FIELD_NAMES",
    "CRITICAL_GRADER_TYPES",
    "FINAL_SUITE_CONTENT_ROLES",
    "GRADER_TYPES",
    "MAX_BATCH_SIZE",
    "MAX_JSON_BYTES",
    "SEVERITIES",
    "CaseFieldError",
    "CaseNotFoundError",
    "DuplicateCaseKeyError",
    "EvaluationCaseRecord",
    "EvaluationCaseValidationError",
    "FinalSuiteContentRestrictedError",
    "SuiteLockedError",
    "SuiteNotFoundError",
    "ValidatedCase",
    "get_case",
    "import_cases",
    "list_cases",
    "update_case",
    "validate_case_input",
    "validate_case_patch",
]

MAX_BATCH_SIZE = 500
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_JSON_DEPTH = 64
CASE_KEY_MAX_LENGTH = 200
CATEGORY_MAX_LENGTH = 100
LANGUAGE_MAX_LENGTH = 35
GROUP_KEY_MAX_LENGTH = 200

# Section 6.14 / 23: the closed grader vocabulary, in the spec's own order.
GRADER_TYPES: tuple[str, ...] = (
    "exact_match",
    "json_schema",
    "tool_name",
    "tool_arguments",
    "tool_sequence",
    "state_transition",
    "forbidden_action",
    "required_refusal",
    "contains_fact",
    "numeric_tolerance",
    "latency",
    "cost",
    "llm_judge",
)
# Section 27 / 69.2: the graders whose verdict does not depend on a model's
# self-report or a judge, so a critical case may rely on them.
CRITICAL_GRADER_TYPES: frozenset[str] = frozenset(
    {
        "tool_name",
        "tool_arguments",
        "tool_sequence",
        "state_transition",
        "forbidden_action",
        "required_refusal",
    }
)
SEVERITIES: tuple[str, ...] = ("low", "medium", "high", "critical")

# Section 69.1: final content is sealed from ordinary trainer roles.
FINAL_SUITE_CONTENT_ROLES: frozenset[Role] = frozenset(
    {Role.EVALUATOR, Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN}
)

_TEXT_FIELDS: dict[str, int] = {
    "case_key": CASE_KEY_MAX_LENGTH,
    "category": CATEGORY_MAX_LENGTH,
    "language": LANGUAGE_MAX_LENGTH,
    "group_key": GROUP_KEY_MAX_LENGTH,
}
_JSON_FIELDS = ("input_json", "expected_json", "grader_config_json")
_REQUIRED_FIELDS = (
    "case_key",
    "category",
    "severity",
    "language",
    "group_key",
    "input_json",
    "expected_json",
    "grader_type",
)
_OPTIONAL_FIELDS = ("critical", "grader_config_json", "weight")
CASE_FIELD_NAMES: frozenset[str] = frozenset(_REQUIRED_FIELDS + _OPTIONAL_FIELDS)
_PATCHABLE_FIELDS: frozenset[str] = CASE_FIELD_NAMES - {"case_key"}

IMPORT_AUDIT_ACTION = "evaluation_case.import"
UPDATE_AUDIT_ACTION = "evaluation_case.update"
_UNIQUE_CASE_KEY_CONSTRAINT = "uq_evaluation_cases_suite_id_case_key"


@dataclass(frozen=True)
class CaseFieldError:
    """One refused field: which row, which field, a stable code. Never the
    submitted value."""

    index: int | None
    field: str | None
    code: str


class EvaluationCaseValidationError(ValueError):
    def __init__(self, errors: list[CaseFieldError]) -> None:
        super().__init__(f"{len(errors)} invalid field(s)")
        self.errors = errors


class SuiteNotFoundError(LookupError):
    """No suite with this id exists in the caller's own organization and
    project (identical for missing, foreign-tenant and wrong-project)."""


class CaseNotFoundError(LookupError):
    """No case with this id exists in this suite (identical for missing and
    foreign)."""


class SuiteLockedError(RuntimeError):
    """The suite is `locked`; its cases can no longer change."""


class DuplicateCaseKeyError(RuntimeError):
    """A `case_key` repeats inside the batch or already exists in the suite."""


class FinalSuiteContentRestrictedError(PermissionError):
    """The caller's role may not read or change a final suite's content."""


@dataclass(frozen=True)
class ValidatedCase:
    case_key: str
    category: str
    severity: str
    critical: bool
    language: str
    group_key: str
    input_json: dict[str, Any]
    expected_json: dict[str, Any]
    grader_type: str
    grader_config_json: dict[str, Any]
    weight: float


class EvaluationCaseRecord(Base):
    __tablename__ = "evaluation_cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    suite_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_key: Mapped[str] = mapped_column(String(CASE_KEY_MAX_LENGTH), nullable=False)
    category: Mapped[str] = mapped_column(String(CATEGORY_MAX_LENGTH), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    critical: Mapped[bool] = mapped_column(Boolean, nullable=False)
    language: Mapped[str] = mapped_column(String(LANGUAGE_MAX_LENGTH), nullable=False)
    group_key: Mapped[str] = mapped_column(String(GROUP_KEY_MAX_LENGTH), nullable=False)
    input_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    expected_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    grader_type: Mapped[str] = mapped_column(String(32), nullable=False)
    grader_config_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Mirrors the migration's unique key so the ORM metadata (and the SQLite
    # tests built from it) enforce the same duplicate rule as Postgres.
    __table_args__ = (UniqueConstraint("suite_id", "case_key", name=_UNIQUE_CASE_KEY_CONSTRAINT),)


# -- validation (pure: no database) -------------------------------------------


def _scalar_text_problem(value: object, max_length: int) -> str | None:
    if not isinstance(value, str):
        return "invalid_type"
    if not value or value != value.strip():
        return "invalid_value"
    if len(value) > max_length:
        return "too_long"
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return "invalid_value"
    if "\x00" in value:
        return "invalid_value"
    return None


def _json_object_problem(value: object) -> str | None:
    """None when `value` is a JSON object that Postgres jsonb can store and
    is within the size and depth bounds; otherwise a stable code."""
    if not isinstance(value, dict):
        return "invalid_type"
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            return "invalid_value"
        if isinstance(node, dict):
            for key, child in node.items():
                if not isinstance(key, str) or _scalar_text_problem_json(key):
                    return "invalid_value"
                stack.append((child, depth + 1))
        elif isinstance(node, list):
            stack.extend((child, depth + 1) for child in node)
        elif isinstance(node, str):
            if _scalar_text_problem_json(node):
                return "invalid_value"
        elif isinstance(node, bool) or node is None:
            continue
        elif isinstance(node, int):
            continue
        elif isinstance(node, float):
            if not math.isfinite(node):
                return "invalid_value"
        else:
            return "invalid_type"
    try:
        # Postgres renders jsonb with ", " and ": " separators; measuring the
        # same form keeps the application bound at least as strict as the
        # database CHECK on `octet_length(<col>::text)`.
        size = len(json.dumps(value, separators=(", ", ": "), ensure_ascii=False).encode("utf-8"))
    except (UnicodeEncodeError, ValueError, RecursionError):
        return "invalid_value"
    if size > MAX_JSON_BYTES:
        return "too_large"
    return None


def _scalar_text_problem_json(text: str) -> bool:
    if "\x00" in text:
        return True
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return True
    return False


def _weight_problem(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "invalid_type"
    try:
        as_float = float(value)
    except OverflowError:
        return "invalid_value"
    if not math.isfinite(as_float) or as_float <= 0:
        return "invalid_value"
    return None


def _field_problem(name: str, value: object) -> str | None:
    if name in _TEXT_FIELDS:
        return _scalar_text_problem(value, _TEXT_FIELDS[name])
    if name == "severity":
        return None if isinstance(value, str) and value in SEVERITIES else "invalid_value"
    if name == "grader_type":
        return None if isinstance(value, str) and value in GRADER_TYPES else "invalid_value"
    if name == "critical":
        return None if isinstance(value, bool) else "invalid_type"
    if name == "weight":
        return _weight_problem(value)
    if name in _JSON_FIELDS:
        problem = _json_object_problem(value)
        if problem is None and name == "expected_json" and not value:
            return "empty_object"
        return problem
    return "unknown_field"


def _oracle_error(
    index: int | None, critical: object, grader_type: object
) -> CaseFieldError | None:
    if (
        critical is True
        and isinstance(grader_type, str)
        and grader_type in GRADER_TYPES
        and grader_type not in CRITICAL_GRADER_TYPES
    ):
        return CaseFieldError(index, "grader_type", "critical_oracle_not_independent")
    return None


def validate_case_input(
    raw: object, index: int
) -> tuple[ValidatedCase | None, list[CaseFieldError]]:
    """Validates one `EvaluationCaseInput` object. Returns the clean case, or
    the per-field errors (never the submitted values)."""
    if not isinstance(raw, dict):
        return None, [CaseFieldError(index, None, "invalid_type")]
    errors: list[CaseFieldError] = []
    if any(not isinstance(key, str) or key not in CASE_FIELD_NAMES for key in raw):
        errors.append(CaseFieldError(index, None, "unknown_field"))
    for name in _REQUIRED_FIELDS:
        if name not in raw:
            errors.append(CaseFieldError(index, name, "required"))
        else:
            problem = _field_problem(name, raw[name])
            if problem is not None:
                errors.append(CaseFieldError(index, name, problem))
    for name in _OPTIONAL_FIELDS:
        if name in raw:
            problem = _field_problem(name, raw[name])
            if problem is not None:
                errors.append(CaseFieldError(index, name, problem))
    oracle = _oracle_error(index, raw.get("critical", False), raw.get("grader_type"))
    if oracle is not None:
        errors.append(oracle)
    if errors:
        return None, errors
    return (
        ValidatedCase(
            case_key=raw["case_key"],
            category=raw["category"],
            severity=raw["severity"],
            critical=raw.get("critical", False),
            language=raw["language"],
            group_key=raw["group_key"],
            input_json=raw["input_json"],
            expected_json=raw["expected_json"],
            grader_type=raw["grader_type"],
            grader_config_json=raw.get("grader_config_json", {}),
            weight=float(raw.get("weight", 1)),
        ),
        [],
    )


def validate_case_patch(raw: object) -> tuple[dict[str, Any], list[CaseFieldError]]:
    """Validates a PATCH body: a non-empty subset of the mutable fields.
    `case_key` is immutable."""
    if not isinstance(raw, dict):
        return {}, [CaseFieldError(None, None, "invalid_type")]
    errors: list[CaseFieldError] = []
    patch: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "case_key":
            errors.append(CaseFieldError(None, "case_key", "immutable"))
        elif not isinstance(key, str) or key not in _PATCHABLE_FIELDS:
            errors.append(CaseFieldError(None, None, "unknown_field"))
        else:
            problem = _field_problem(key, value)
            if problem is not None:
                errors.append(CaseFieldError(None, key, problem))
            else:
                patch[key] = float(value) if key == "weight" else value
    if not raw:
        errors.append(CaseFieldError(None, None, "empty_patch"))
    return patch, errors


# -- persistence --------------------------------------------------------------


def _load_suite(
    session: Session,
    context: TenantContext,
    project_id: uuid.UUID,
    suite_id: uuid.UUID,
    *,
    role: Role,
    for_update: bool,
) -> EvaluationSuite:
    if not isinstance(suite_id, uuid.UUID) or not isinstance(project_id, uuid.UUID):
        raise SuiteNotFoundError("project_id and suite_id must be uuid.UUID")
    statement = select(EvaluationSuite).where(
        EvaluationSuite.id == suite_id,
        EvaluationSuite.project_id == project_id,
        EvaluationSuite.organization_id == context.organization_id,
    )
    if for_update:
        statement = statement.with_for_update()
    result = execute_tenant_scoped(session, context.organization_id, statement)
    suite: EvaluationSuite | None = result.scalars().one_or_none()
    if suite is None:
        raise SuiteNotFoundError("evaluation suite not found")
    if suite.purpose == PURPOSE_FINAL and role not in FINAL_SUITE_CONTENT_ROLES:
        raise FinalSuiteContentRestrictedError("final suite content is restricted")
    return suite


def _is_duplicate_key_violation(exc: IntegrityError) -> bool:
    diag = getattr(exc.orig, "diag", None)
    if getattr(diag, "constraint_name", None) == _UNIQUE_CASE_KEY_CONSTRAINT:
        return True
    return "evaluation_cases.suite_id, evaluation_cases.case_key" in str(exc.orig)


def import_cases(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    suite_id: uuid.UUID,
    role: Role,
    cases: list[ValidatedCase],
    actor_type: str,
    actor_id: str,
    request_id: str,
) -> list[EvaluationCaseRecord]:
    """All-or-nothing insert of already-validated cases into a draft suite.
    Raises `SuiteNotFoundError`, `FinalSuiteContentRestrictedError`,
    `SuiteLockedError` or `DuplicateCaseKeyError`; on any raise nothing was
    written (the caller's transaction rolls back)."""
    if not 1 <= len(cases) <= MAX_BATCH_SIZE:
        raise ValueError("cases must hold 1..500 entries")
    suite = _load_suite(session, context, project_id, suite_id, role=role, for_update=True)
    if suite.status != "draft":
        raise SuiteLockedError("evaluation suite is locked")

    keys = [case.case_key for case in cases]
    if len(set(keys)) != len(keys):
        raise DuplicateCaseKeyError("case_key repeats inside the batch")
    existing = execute_tenant_scoped(
        session,
        context.organization_id,
        select(EvaluationCaseRecord.case_key).where(
            EvaluationCaseRecord.suite_id == suite_id,
            EvaluationCaseRecord.organization_id == context.organization_id,
            EvaluationCaseRecord.case_key.in_(keys),
        ),
    ).first()
    if existing is not None:
        raise DuplicateCaseKeyError("case_key already exists in this suite")

    now = datetime.now(UTC)
    records = [
        EvaluationCaseRecord(
            id=uuid.uuid4(),
            organization_id=context.organization_id,
            project_id=project_id,
            suite_id=suite_id,
            case_key=case.case_key,
            category=case.category,
            severity=case.severity,
            critical=case.critical,
            language=case.language,
            group_key=case.group_key,
            input_json=case.input_json,
            expected_json=case.expected_json,
            grader_type=case.grader_type,
            grader_config_json=case.grader_config_json,
            weight=case.weight,
            created_by=actor_id,
            created_at=now,
            updated_at=now,
        )
        for case in cases
    ]
    session.add_all(records)
    try:
        session.flush()
    except IntegrityError as exc:
        if _is_duplicate_key_violation(exc):
            raise DuplicateCaseKeyError("case_key already exists in this suite") from exc
        raise
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=IMPORT_AUDIT_ACTION,
        resource_type="evaluation_suite",
        resource_id=str(suite_id),
        request_id=request_id,
        details={
            "project_id": str(project_id),
            "count": len(records),
            "case_ids": [str(record.id) for record in records],
        },
    )
    session.flush()
    return records


def list_cases(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    suite_id: uuid.UUID,
    role: Role,
    limit: int,
    after: str | None,
) -> tuple[list[EvaluationCaseRecord], str | None]:
    """Cases ordered by `case_key`, `limit` at a time. Returns the page and the
    `case_key` to pass as `after` for the next page (None on the last page)."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_BATCH_SIZE:
        raise ValueError("limit must be an integer between 1 and 500")
    _load_suite(session, context, project_id, suite_id, role=role, for_update=False)
    statement = select(EvaluationCaseRecord).where(
        EvaluationCaseRecord.suite_id == suite_id,
        EvaluationCaseRecord.organization_id == context.organization_id,
    )
    if after is not None:
        statement = statement.where(EvaluationCaseRecord.case_key > after)
    statement = statement.order_by(EvaluationCaseRecord.case_key).limit(limit + 1)
    rows = list(execute_tenant_scoped(session, context.organization_id, statement).scalars().all())
    page = rows[:limit]
    return page, (page[-1].case_key if len(rows) > limit else None)


def _fetch_case(
    session: Session,
    context: TenantContext,
    suite_id: uuid.UUID,
    case_id: uuid.UUID,
    *,
    for_update: bool,
) -> EvaluationCaseRecord:
    if not isinstance(case_id, uuid.UUID):
        raise CaseNotFoundError("case_id must be a uuid.UUID")
    statement = select(EvaluationCaseRecord).where(
        EvaluationCaseRecord.id == case_id,
        EvaluationCaseRecord.suite_id == suite_id,
        EvaluationCaseRecord.organization_id == context.organization_id,
    )
    if for_update:
        statement = statement.with_for_update()
    case: EvaluationCaseRecord | None = (
        execute_tenant_scoped(session, context.organization_id, statement).scalars().one_or_none()
    )
    if case is None:
        raise CaseNotFoundError("evaluation case not found")
    return case


def get_case(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    suite_id: uuid.UUID,
    case_id: uuid.UUID,
    role: Role,
) -> EvaluationCaseRecord:
    _load_suite(session, context, project_id, suite_id, role=role, for_update=False)
    return _fetch_case(session, context, suite_id, case_id, for_update=False)


def update_case(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    suite_id: uuid.UUID,
    case_id: uuid.UUID,
    role: Role,
    patch: dict[str, Any],
    actor_type: str,
    actor_id: str,
    request_id: str,
) -> EvaluationCaseRecord:
    """Edits the named fields of a case in a draft suite. `patch` is already
    validated field by field; the merged row is re-checked here (a critical
    case must keep an independent grader)."""
    if not patch or not set(patch) <= _PATCHABLE_FIELDS:
        raise ValueError("patch must name 1+ mutable fields")
    suite = _load_suite(session, context, project_id, suite_id, role=role, for_update=True)
    if suite.status != "draft":
        raise SuiteLockedError("evaluation suite is locked")
    case = _fetch_case(session, context, suite_id, case_id, for_update=True)

    merged_critical = patch.get("critical", case.critical)
    merged_grader = patch.get("grader_type", case.grader_type)
    oracle = _oracle_error(None, merged_critical, merged_grader)
    if oracle is not None:
        raise EvaluationCaseValidationError([oracle])

    for name, value in patch.items():
        setattr(case, name, value)
    case.updated_at = datetime.now(UTC)
    session.flush()
    write_audit_event(
        session,
        organization_id=context.organization_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=UPDATE_AUDIT_ACTION,
        resource_type="evaluation_case",
        resource_id=str(case.id),
        request_id=request_id,
        details={"suite_id": str(suite_id), "fields": sorted(patch)},
    )
    session.flush()
    return case
