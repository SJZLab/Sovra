"""FTF-R004-P3 -- map a locked development EvaluationSuite's cases to Inspect `Sample`s.

Section 78.2 / 66.3: the cases an Inspect run executes are the cases of one
suite the Factory already locked, read through the tenant-scoped FTF-0602-P1
store -- never a free-form list the caller assembled.

* `load_locked_development_suite` reads the suite and its cases through
  `get_evaluation_suite` / `list_cases` (tenant context, row-level security).
  It refuses, before reading any case content:
  - a `final`-purpose suite -- `final_suite_requires_freeze` (sealed content is
    reachable only after the FTF-H020 candidate freeze, which does not exist yet);
  - a suite that is not `locked` -- `suite_not_locked`;
  - any other purpose -- `unsupported_suite_purpose` (default deny).
* `samples_from_cases` is the pure mapping. Each Sample's id is the `case_key`;
  its metadata carries case_id, category, severity, critical, group_key, language,
  grader_type and the suite / manifest ids. A repeated case id is
  `duplicate_case_id`; an empty suite is `empty_suite`.
* Case input is carried as an immutable `declared_inputs` tuple (dicts become
  tuples of pairs, lists become tuples) so it can sit in an `AdapterInvokeRequest`.

Errors are `CaseAdapterError` with a stable `code`; the message never holds case
content.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.db.tenant_context import TenantContext
from app.evaluation.evaluation_case_store import EvaluationCaseRecord, list_cases
from app.evaluation.evaluation_suite_crud import get_evaluation_suite
from inspect_ai.dataset import Sample
from sqlalchemy.orm import Session

__all__ = [
    "CODE_DUPLICATE_CASE_ID",
    "CODE_EMPTY_SUITE",
    "CODE_FINAL_SUITE",
    "CODE_INVALID_CASE",
    "CODE_SUITE_NOT_LOCKED",
    "CODE_UNSUPPORTED_PURPOSE",
    "BridgeCase",
    "CaseAdapterError",
    "SuiteCaseSet",
    "freeze_declared_inputs",
    "load_locked_development_suite",
    "samples_from_cases",
]

CODE_SUITE_NOT_LOCKED = "suite_not_locked"
CODE_FINAL_SUITE = "final_suite_requires_freeze"
CODE_UNSUPPORTED_PURPOSE = "unsupported_suite_purpose"
CODE_DUPLICATE_CASE_ID = "duplicate_case_id"
CODE_EMPTY_SUITE = "empty_suite"
CODE_INVALID_CASE = "invalid_case"

_PAGE = 500
_MAX_PAGES = 10_000
_MAX_FREEZE_DEPTH = 64


class CaseAdapterError(Exception):
    """A suite or case cannot be handed to Inspect. `code` is stable; the
    message holds no case content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class BridgeCase:
    """One case as the runtime bridge needs it. `declared_inputs` is immutable;
    `expected_json` is deliberately not here -- the bridge never hands an
    expectation to a target runtime."""

    case_key: str
    case_id: str
    category: str
    severity: str
    critical: bool
    group_key: str
    language: str
    grader_type: str
    declared_inputs: tuple[tuple[str, object], ...]


@dataclass(frozen=True)
class SuiteCaseSet:
    suite_id: str
    project_id: str
    agent_manifest_id: str
    purpose: str
    grading_logic_version: int
    cases: tuple[BridgeCase, ...]
    samples: tuple[Sample, ...]


def _freeze(value: object, depth: int = 0) -> object:
    if depth > _MAX_FREEZE_DEPTH:
        raise CaseAdapterError(CODE_INVALID_CASE)
    if isinstance(value, Mapping):
        items: list[tuple[str, object]] = []
        for key, inner in value.items():
            if type(key) is not str:
                raise CaseAdapterError(CODE_INVALID_CASE)
            items.append((key, _freeze(inner, depth + 1)))
        return tuple(items)
    if isinstance(value, list | tuple):
        return tuple(_freeze(inner, depth + 1) for inner in value)
    if value is None or type(value) in (bool, int, float, str):
        return value
    raise CaseAdapterError(CODE_INVALID_CASE)


def freeze_declared_inputs(input_json: object) -> tuple[tuple[str, object], ...]:
    """The case's `input_json` object as the immutable `declared_inputs` shape."""
    if not isinstance(input_json, Mapping):
        raise CaseAdapterError(CODE_INVALID_CASE)
    frozen = _freeze(input_json)
    assert isinstance(frozen, tuple)
    return frozen


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _text(value: object) -> str:
    if type(value) is not str or not value:
        raise CaseAdapterError(CODE_INVALID_CASE)
    return value


def samples_from_cases(
    *,
    suite_id: str,
    project_id: str,
    agent_manifest_id: str,
    purpose: str,
    grading_logic_version: int,
    records: Sequence[EvaluationCaseRecord],
) -> SuiteCaseSet:
    """The pure mapping. Does not check the suite's status or purpose -- the
    loader does, before it reads a case."""
    if not records:
        raise CaseAdapterError(CODE_EMPTY_SUITE)
    seen_keys: set[str] = set()
    seen_ids: set[str] = set()
    cases: list[BridgeCase] = []
    samples: list[Sample] = []
    for record in records:
        case_key = _text(record.case_key)
        case_id = str(record.id)
        if case_key in seen_keys or case_id in seen_ids:
            raise CaseAdapterError(CODE_DUPLICATE_CASE_ID)
        seen_keys.add(case_key)
        seen_ids.add(case_id)
        if type(record.critical) is not bool:
            raise CaseAdapterError(CODE_INVALID_CASE)
        case = BridgeCase(
            case_key=case_key,
            case_id=case_id,
            category=_text(record.category),
            severity=_text(record.severity),
            critical=record.critical,
            group_key=_text(record.group_key),
            language=_text(record.language),
            grader_type=_text(record.grader_type),
            declared_inputs=freeze_declared_inputs(record.input_json),
        )
        cases.append(case)
        samples.append(
            Sample(
                id=case_key,
                input=_canonical(record.input_json),
                target=_canonical(record.expected_json),
                metadata={
                    "case_id": case_id,
                    "category": case.category,
                    "severity": case.severity,
                    "critical": case.critical,
                    "group_key": case.group_key,
                    "language": case.language,
                    "grader_type": case.grader_type,
                    "suite_id": suite_id,
                    "agent_manifest_id": agent_manifest_id,
                },
            )
        )
    return SuiteCaseSet(
        suite_id=suite_id,
        project_id=project_id,
        agent_manifest_id=agent_manifest_id,
        purpose=purpose,
        grading_logic_version=grading_logic_version,
        cases=tuple(cases),
        samples=tuple(samples),
    )


def load_locked_development_suite(
    session: Session,
    context: TenantContext,
    *,
    project_id: uuid.UUID,
    suite_id: uuid.UUID,
    role: Any,
) -> SuiteCaseSet:
    """Reads one locked development-purpose suite and all of its cases through
    the tenant-scoped store. Sealed (`final`) content is never read."""
    suite = get_evaluation_suite(session, context, project_id, suite_id)
    if suite.purpose == "final":
        raise CaseAdapterError(CODE_FINAL_SUITE)
    if suite.purpose != "development":
        raise CaseAdapterError(CODE_UNSUPPORTED_PURPOSE)
    if suite.status != "locked":
        raise CaseAdapterError(CODE_SUITE_NOT_LOCKED)

    records: list[EvaluationCaseRecord] = []
    after: str | None = None
    for _ in range(_MAX_PAGES):
        page, next_after = list_cases(
            session,
            context,
            project_id=project_id,
            suite_id=suite_id,
            role=role,
            limit=_PAGE,
            after=after,
        )
        records.extend(page)
        if next_after is None:
            break
        if after is not None and next_after <= after:
            raise CaseAdapterError(CODE_INVALID_CASE)
        after = next_after
    else:
        raise CaseAdapterError(CODE_INVALID_CASE)

    return samples_from_cases(
        suite_id=str(suite.id),
        project_id=str(suite.project_id),
        agent_manifest_id=str(suite.agent_manifest_id),
        purpose=suite.purpose,
        grading_logic_version=suite.grading_logic_version,
        records=records,
    )
