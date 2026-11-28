"""FTF-R004-P4 -- Inspect log normalization, complete accounting and the sealed-data guard.

Section 69.1-69.2, 78.2 paragraphs 3-5, section 30 "Final-test protection", Q16-Q18. Acceptance:
"Normalize complete Inspect case/log results, protect sealed final data and independently verify
business-state outcome and statistics." Negative: "edited score log, missing/cancelled cases,
unapproved judge or final content in normal MLflow/Argilla cannot pass".

`normalize_bridge_run` reads the log of one FTF-R004-P3 run only through Inspect's own log API
(`read_eval_log`) and returns a content-free `NormalizedRun`:

* every scheduled case maps to exactly one of `pass`, `model_fail`, `tool_fail`, `infra_error`,
  `timeout`, `cancelled`, `not_run`; a run with any `cancelled` or `not_run` case is
  `incomplete_run` and blocked (the summary still lists every case, Law 17);
* a critical case whose model or tool produced an effect -- recorded by P3, or found in the
  adapter's own state -- is never `infra_error`: it is `tool_fail`. A critical case whose
  adapter raised and whose state cannot be collected is `tool_fail` too (the effect is unknown);
* the numerator (passes) and denominator (scheduled cases) are recounted from the per-case rows;
  Inspect's own summary metrics are never read. The Wilson / bootstrap gates stay with
  FTF-H021 / FTF-0618.

Refusals (`ResultReaderError.code`, stable, content-free):

* `score_log_tampered` -- the log's bytes no longer hash to the sha256 P3 recorded when it wrote
  the log, the log is outside the run's private log directory, or its plan, model or sample set
  is not the one P3 ran;
* `outcome_mismatch` -- an Inspect score, or the log's recorded adapter status, disagrees with the
  P3 record, or the record disagrees with the adapter's independent `collect_outcome` business
  state (case id, status or business-outcome digest). Every invoked case is re-checked, whatever
  its status;
* `unapproved_judge` -- a score from any scorer other than P3's own adapter-status scorer (a
  model-graded score) whose judge route identity is not one of the approved identities passed in.
  Default deny: with none passed, every model-graded score is refused. Approving a judge is
  FTF-0616/0617's;
* `final_suite_requires_freeze` -- a final-purpose suite. Sealed final content is read only by
  the FTF-H020 sealed worker after the candidate freeze, which does not exist yet;
* `result_reader_invalid_request` -- inputs that are not the P3 run, its loaded suite and adapter;
* `outbound_call_blocked` -- the business-state re-check (adapter calls) runs under P3's network
  and process guard.

The raw log is referenced only by its path and sha256; no input, expected value, prompt or tool
content reaches the summary.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from inspect_ai.log import EvalLog, read_eval_log
from inspect_ai.scorer import CORRECT, INCORRECT

from app.target_connectors.production_faithful_target_agent_harness_contract import (
    RuntimeAdapterProtocol,
)
from workers.evaluation.adapters.inspect_adapter.case_adapter import (
    CODE_FINAL_SUITE,
    SuiteCaseSet,
    is_loaded_suite_case_set,
)
from workers.evaluation.adapters.inspect_adapter.runtime_bridge import (
    NATIVE_SOLVER_NAME,
    BridgeError,
    BridgeRunResult,
    CaseRunRecord,
    _NetworkGuard,
    _outcome_digest,
    _require_invoke_result,
)

__all__ = [
    "CATEGORIES",
    "CODE_INCOMPLETE_RUN",
    "CODE_INVALID_REQUEST",
    "CODE_OUTCOME_MISMATCH",
    "CODE_TAMPERED",
    "CODE_UNAPPROVED_JUDGE",
    "JUDGE_ROUTE_METADATA_KEY",
    "NormalizedCase",
    "NormalizedRun",
    "ResultReaderError",
    "normalize_bridge_run",
]

CATEGORIES = ("pass", "model_fail", "tool_fail", "infra_error", "timeout", "cancelled", "not_run")

CODE_TAMPERED = "score_log_tampered"
CODE_OUTCOME_MISMATCH = "outcome_mismatch"
CODE_UNAPPROVED_JUDGE = "unapproved_judge"
CODE_INCOMPLETE_RUN = "incomplete_run"
CODE_INVALID_REQUEST = "result_reader_invalid_request"

# a model-graded score names its judge route here (FTF-0617's route fingerprint)
JUDGE_ROUTE_METADATA_KEY = "judge_route_fingerprint"

# The exact scorer name P3 writes (runtime_bridge `_SCORER_NAME`); the model-less id P3 runs
# under is compared as the literal "none/none" (runtime_bridge `_MODEL_LESS`).
_P3_SCORER = "adapter_reported_status"

_ERROR = object()  # the category of an `error` record comes from its error classification
# P3 record status -> (category, how its business state is re-checked):
#   True: strictly (case id, status, outcome digest); None: best effort, for its effects only
#   (the adapter raised, so there may be no state); False: never invoked, nothing to re-check.
_STATUS: dict[str, tuple[object, bool | None]] = {
    "completed": ("pass", True),
    "error": (_ERROR, True),
    "cancelled": ("cancelled", True),
    "deadline_exceeded": ("timeout", True),
    "preflight_refused": ("not_run", False),
    "adapter_exception": ("infra_error", None),
}
_ERROR_CLASSIFICATION_CATEGORY = {
    "refusal": "model_fail",
    "malformed_output": "model_fail",
    "fatal": "tool_fail",
    "transient": "infra_error",
    "timeout": "timeout",
}
_LIMIT_CATEGORY = {"time": "timeout", "working": "timeout"}  # any other limit: cancelled
_CRITICAL_EFFECT_RELABEL = {"infra_error": "tool_fail"}
_UNKNOWN_EFFECT = ("<unknown: adapter state not collectable>",)


class ResultReaderError(Exception):
    """The run's results cannot be trusted. `code` is stable; the message holds no case content."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class NormalizedCase:
    case_key: str
    case_id: str
    critical: bool
    category: str
    record_status: str
    business_outcome_sha256: str | None
    side_effect_count: int


@dataclass(frozen=True)
class NormalizedRun:
    """Content-free: ids, categories, counts and digests only. A blocked run's numerator is not
    a result: read `blocked` first."""

    run_id: str
    suite_id: str
    agent_manifest_id: str
    log_path: str
    log_sha256: str
    cases: tuple[NormalizedCase, ...]
    counts: tuple[tuple[str, int], ...]
    numerator: int
    denominator: int
    blocking_codes: tuple[str, ...]
    approved_judge_scores: int

    @property
    def blocked(self) -> bool:
        return bool(self.blocking_codes)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_log(result: BridgeRunResult) -> EvalLog:
    """Reads the log exactly once into memory, checks its hash, and hands those same bytes to
    Inspect's own reader (through a private copy), so the parsed log is the hashed log."""
    log_path = Path(result.log_path)
    if log_path.parent.resolve() != Path(result.log_dir).resolve():
        raise ResultReaderError(CODE_TAMPERED, "log destination")
    try:
        data = log_path.read_bytes()
    except OSError as exc:
        raise ResultReaderError(CODE_TAMPERED, "log unreadable") from exc
    if _sha256(data) != result.log_sha256:
        raise ResultReaderError(CODE_TAMPERED, "sha256")
    workspace = Path(tempfile.mkdtemp(prefix="sovra-inspect-read-"))
    copy = workspace / log_path.name
    try:
        os.chmod(workspace, 0o700)
        copy.write_bytes(data)
        return read_eval_log(str(copy))
    except Exception as exc:  # noqa: BLE001 -- an unparseable log is a tampered log
        raise ResultReaderError(CODE_TAMPERED, "unparseable") from exc
    finally:
        copy.unlink(missing_ok=True)
        workspace.rmdir()


def _check_log_shape(log: EvalLog, scheduled: frozenset[str]) -> None:
    steps = [step.solver for step in (log.plan.steps if log.plan else ())]
    if steps != [NATIVE_SOLVER_NAME] or log.eval.model != "none/none":
        raise ResultReaderError(CODE_TAMPERED, "plan")
    if not {str(sample.id) for sample in (log.samples or ())} <= scheduled:
        raise ResultReaderError(CODE_TAMPERED, "sample set")


def _judge_scores(log: EvalLog, approved: frozenset[str]) -> int:
    count = 0
    for sample in log.samples or ():
        for name, score in (sample.scores or {}).items():
            if name == "adapter_reported_status":  # P3's own scorer (`_P3_SCORER`)
                continue
            route = (score.metadata or {}).get(JUDGE_ROUTE_METADATA_KEY)
            if type(route) is not str or route not in approved:
                raise ResultReaderError(CODE_UNAPPROVED_JUDGE, "model-graded score")
            count += 1
    return count


def _base_category(record: CaseRunRecord) -> str:
    entry = _STATUS.get(record.status)
    if entry is None:
        raise ResultReaderError(CODE_OUTCOME_MISMATCH, "record status")
    if entry[0] is _ERROR:
        return _ERROR_CLASSIFICATION_CATEGORY.get(record.error_classification, "infra_error")
    return str(entry[0])


def _recheck_business_state(
    adapter: RuntimeAdapterProtocol, record: CaseRunRecord
) -> tuple[str, ...]:
    """The adapter's independent business state for one case; returns its side effects."""
    entry = _STATUS.get(record.status)
    if entry is None:
        raise ResultReaderError(CODE_OUTCOME_MISMATCH, "record status")
    if entry[1] is False:
        return ()
    try:
        collected = adapter.collect_outcome(record.case_key)
        _require_invoke_result(collected)
    except BridgeError as exc:
        if entry[1] is None:
            return _UNKNOWN_EFFECT
        raise ResultReaderError(CODE_OUTCOME_MISMATCH, exc.code) from exc
    except Exception as exc:  # noqa: BLE001 -- an uncollectable state is never a clean state
        if entry[1] is None:
            return _UNKNOWN_EFFECT
        raise ResultReaderError(CODE_OUTCOME_MISMATCH, "uncollectable") from exc
    if entry[1] is None:
        return collected.side_effects
    observed = (
        collected.case_id,
        _STATUS.get(collected.status, ("",))[0] == "pass",
        _outcome_digest(collected),
    )
    recorded = (record.case_key, entry[0] == "pass", record.business_outcome_sha256)
    if observed != recorded:
        raise ResultReaderError(CODE_OUTCOME_MISMATCH, "business state")
    return collected.side_effects


def _normalize_case(
    sample: object,
    critical: bool,
    record: CaseRunRecord | None,
    effects: tuple[str, ...],
) -> str:
    if sample is None or record is None:
        return "not_run"
    score = (getattr(sample, "scores", None) or {}).get(_P3_SCORER)
    metadata = getattr(sample, "metadata", None) or {}
    limit = getattr(sample, "limit", None)
    if limit is not None:
        category = _LIMIT_CATEGORY.get(limit.type, "cancelled")
    elif getattr(sample, "error", None) is not None:
        category = "infra_error"
    elif score is None:
        category = "not_run"
    else:
        category = _base_category(record)
        # the log row must say exactly what P3 recorded: the adapter status, and C only for a
        # completed case
        logged = (metadata.get("adapter_status"), score.value)
        expected = (record.status, CORRECT if _STATUS[record.status][0] == "pass" else INCORRECT)
        if logged != expected:
            raise ResultReaderError(CODE_OUTCOME_MISMATCH, "score")
    if critical and (effects or record.side_effect_count):
        # a critical case's model/tool effect is never relabelled an infrastructure error
        category = _CRITICAL_EFFECT_RELABEL.get(category, category)
    return category


def normalize_bridge_run(
    result: BridgeRunResult,
    case_set: SuiteCaseSet,
    adapter: RuntimeAdapterProtocol,
    *,
    approved_judge_routes: Iterable[str] = (),
) -> NormalizedRun:
    """Normalizes one P3 run, or raises `ResultReaderError`. See the module docstring."""
    if type(result) is not BridgeRunResult:
        raise ResultReaderError(CODE_INVALID_REQUEST, "result")
    if type(case_set) is SuiteCaseSet and case_set.purpose == "final":
        raise ResultReaderError(CODE_FINAL_SUITE)
    if not is_loaded_suite_case_set(case_set) or case_set.purpose != "development":
        raise ResultReaderError(CODE_INVALID_REQUEST, "case_set")
    if (case_set.suite_id, case_set.agent_manifest_id) != (
        result.suite_id,
        result.agent_manifest_id,
    ):
        raise ResultReaderError(CODE_INVALID_REQUEST, "suite")
    if not isinstance(approved_judge_routes, list | tuple | set | frozenset):
        raise ResultReaderError(CODE_INVALID_REQUEST, "approved_judge_routes")
    approved = frozenset(approved_judge_routes)
    if not all(type(route) is str and route for route in approved):
        raise ResultReaderError(CODE_INVALID_REQUEST, "approved_judge_routes")

    expected_ids = {case.case_key: case.case_id for case in case_set.cases}
    records = {record.case_key: record for record in result.cases}
    if len(records) != len(result.cases) or any(
        expected_ids.get(key) != record.case_id for key, record in records.items()
    ):
        raise ResultReaderError(CODE_TAMPERED, "case records")
    log = _read_log(result)
    _check_log_shape(log, frozenset(expected_ids))
    judged = _judge_scores(log, approved)
    samples = {str(sample.id): sample for sample in (log.samples or ())}

    effects: dict[str, tuple[str, ...]] = {}
    with _NetworkGuard() as guard:
        try:
            for record in result.cases:
                effects[record.case_key] = _recheck_business_state(adapter, record)
        finally:
            if guard.attempts:
                raise BridgeError("outbound_call_blocked", ",".join(sorted(set(guard.events))))

    cases = []
    for case in case_set.cases:
        found = records.get(case.case_key)
        cases.append(
            NormalizedCase(
                case_key=case.case_key,
                case_id=case.case_id,
                critical=case.critical,
                category=_normalize_case(
                    samples.get(case.case_key),
                    case.critical,
                    found,
                    effects.get(case.case_key, ()),
                ),
                record_status=found.status if found else "",
                business_outcome_sha256=found.business_outcome_sha256 if found else None,
                side_effect_count=found.side_effect_count if found else 0,
            )
        )
    tally = Counter(case.category for case in cases)
    return NormalizedRun(
        run_id=result.run_id,
        suite_id=result.suite_id,
        agent_manifest_id=result.agent_manifest_id,
        log_path=result.log_path,
        log_sha256=result.log_sha256,
        cases=tuple(cases),
        counts=tuple((name, tally[name]) for name in CATEGORIES),
        numerator=tally["pass"],
        denominator=len(cases),
        blocking_codes=(CODE_INCOMPLETE_RUN,) if tally["cancelled"] + tally["not_run"] else (),
        approved_judge_scores=judged,
    )
