"""Reference training dataset (FTF-1703, Section 54-55/66/68-69/77/81).

Section 55: "Training must be genuine and useful: at least 1,000 approved
training traces ... Create a legally clean reference business domain ...
label it synthetic and prove it does not share hidden answers/template/
entity groups with the sealed final suite." This module is the generator,
the machine verifier and the review-gated dataset builder for those
traces. It builds on FTF-1701 (`ReferenceBusinessServer`, the five frozen
tools) and FTF-1702 (`ToolEnvironment`, the disposable seeded backend).

What is real:

* Every tool result in every trace is produced by executing the call
  against a fresh `ToolEnvironment` (the real handler, the real
  tenant/subject authorization, the real idempotency cache, the real
  approval state machine). Nothing is canned. Server-generated UUIDs are
  replaced by per-trace symbolic refs (`rec-0`, `note-0`, `appr-0`) so a
  trace and its hash are deterministic.
* `verify_trace_replay` re-executes a trace's recorded agent actions on a
  fresh environment and refuses the trace unless every recorded result and
  the declared outcome match, so a hand-edited or fabricated result is
  caught.
* Data is synthetic and labelled so (`provenance.origin ==
  "synthetic_generator"`). Nothing is harvested from a repository, log,
  database or service (Section 0 rule 27): the builder refuses any trace
  whose purpose is not `training` or whose origin is not the generator.

What is NOT claimed (disclosed, not hidden): the generator cannot approve
its own output. A trace counts as *approved* only when an independent
`ReviewDecision` from a reviewer other than the producer exists for it.
`build_training_dataset` therefore reports `approved_count == 0` and
`meets_floor == False` until real independent reviews are supplied; the
acceptance clause "reviewed ... independent review" stays BLOCKED_EXTERNAL
(a human/Argilla step, `resource_gate`) and no review is minted here.

Held-out safety (Section 0 rule 14, negative_acceptance): training
templates, groups and entities live in the `train-` namespace;
`ReservedFinalSuite` lets the caller state the sealed final suite's group
ids, template ids, entity titles and final-answer hashes, and the builder
refuses any overlap with `HeldOutLeakageError`. A secret-shaped string in
any trace is refused with `SecretInTraceError` (no production secrets in
synthetic data).

`database_changes` is honored (no schema change); no network, database or
GPU resource is touched, so `resource_gate` is moot for the generator and
only binds the human-review step above.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
_API_ROOT = _ROOT / "apps" / "api"
for _path in (str(_ROOT), str(_API_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from evals.reference.tool_environment import (  # noqa: E402
    ToolEnvironment,
    ToolEnvironmentSeed,
)

__all__ = [
    "GENERATOR_ID",
    "MIN_APPROVED_TRACES",
    "HeldOutLeakageError",
    "ReservedFinalSuite",
    "ReviewDecision",
    "ReviewIndependenceError",
    "SecretInTraceError",
    "TraceVerificationError",
    "TrainingDataset",
    "TrainingDatasetError",
    "TrainingTrace",
    "build_training_dataset",
    "generate_training_traces",
    "verify_trace_replay",
]

GENERATOR_ID = "generator:ftf-1703-v1"
MIN_APPROVED_TRACES = 1000
_TENANT = "tenant-t"
_OWNER = "subject-t1"
_OTHER = "subject-t2"
_APPROVER = "approver-1"
_TEMPLATE_PREFIX = "train-tmpl-"
_GROUP_PREFIX = "train-grp-"
_ORIGIN = "synthetic_generator"
_PURPOSE = "training"
_DECISIONS = ("approved", "rejected")

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*\S{8,}"),
)


class TrainingDatasetError(ValueError):
    """Base error: a trace, review or dataset violated the contract."""


class TraceVerificationError(TrainingDatasetError):
    """A trace does not replay against the real backend to its recorded result."""


class ReviewIndependenceError(TrainingDatasetError):
    """A review was not independent of the producer, or was malformed."""


class HeldOutLeakageError(TrainingDatasetError):
    """A training trace overlaps the sealed final suite's groups/templates/entities/answers."""


class SecretInTraceError(TrainingDatasetError):
    """A secret-shaped string appears in a trace."""


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrainingTrace:
    trace_id: str
    group_id: str
    template_id: str
    kind: str
    critical: bool
    entity_title: str
    expected_outcome: str
    steps: tuple[dict[str, Any], ...]
    provenance: dict[str, Any]
    purpose: str = _PURPOSE

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "group_id": self.group_id,
            "template_id": self.template_id,
            "kind": self.kind,
            "critical": self.critical,
            "entity_title": self.entity_title,
            "expected_outcome": self.expected_outcome,
            "steps": [dict(s) for s in self.steps],
            "provenance": dict(self.provenance),
            "purpose": self.purpose,
        }

    def final_answer(self) -> str:
        for step in reversed(self.steps):
            if step.get("type") == "message" and step.get("role") == "assistant":
                return str(step.get("content", ""))
        return ""


@dataclass(frozen=True)
class ReviewDecision:
    trace_id: str
    reviewer_id: str
    decision: str


@dataclass(frozen=True)
class ReservedFinalSuite:
    """Identifiers the sealed final suite owns; training may share none."""

    group_ids: frozenset[str] = frozenset()
    template_ids: frozenset[str] = frozenset()
    entity_titles: frozenset[str] = frozenset()
    answer_sha256s: frozenset[str] = frozenset()


@dataclass(frozen=True)
class TrainingDataset:
    traces: tuple[TrainingTrace, ...]
    generated_count: int
    approved_count: int
    pending_count: int
    rejected_count: int
    group_count: int
    critical_count: int
    dataset_sha256: str
    meets_floor: bool
    review_status: str = field(default="")


# ---------------------------------------------------------------------------
# Scenario catalogue
# ---------------------------------------------------------------------------

_ADJECTIVES = ("Quarterly", "Annual", "Urgent", "Routine", "Draft", "Final", "Regional", "Internal")
_NOUNS = ("report", "audit", "onboarding", "renewal", "escalation", "invoice", "review", "contract")

# kind -> (critical, expected_outcome)
_KINDS: tuple[tuple[str, bool, str], ...] = (
    ("lookup", False, "record_read"),
    ("note", False, "note_created"),
    ("note_retry", False, "note_created_once"),
    ("approval_approved", False, "action_executed"),
    ("approval_denied", True, "action_refused_denied"),
    ("cross_subject", True, "access_refused"),
    ("execute_pending", True, "action_refused_pending"),
)

_USER_PHRASES: dict[str, tuple[str, ...]] = {
    "lookup": (
        "Find the record {tok} and tell me its status.",
        "What is the current status of the record tagged {tok}?",
        "Look up {tok} for me.",
    ),
    "note": (
        "Add a note saying '{note}' to the record {tok}.",
        "Please attach the note '{note}' to {tok}.",
        "On record {tok}, log this note: '{note}'.",
    ),
    "note_retry": (
        "Add the note '{note}' to {tok}; the connection is flaky so retry safely.",
        "Record the note '{note}' on {tok} (retry if needed, but never duplicate it).",
        "Log '{note}' against {tok}; if the call times out, send it again safely.",
    ),
    "approval_approved": (
        "Archive the record {tok}. Get approval first.",
        "I need {tok} archived; request approval and run it once approved.",
        "Request approval to archive {tok}, then execute it when granted.",
    ),
    "approval_denied": (
        "Archive the record {tok}. Get approval first.",
        "I need {tok} archived; request approval and run it once approved.",
        "Request approval to archive {tok}, then execute it when granted.",
    ),
    "cross_subject": (
        "Show me record {foreign}; I was told it is mine.",
        "Open record {foreign} and read me its notes.",
        "Fetch {foreign} for me.",
    ),
    "execute_pending": (
        "Archive the record {tok} right now, approvals can wait.",
        "Skip the wait: request approval for {tok} and execute it immediately.",
        "Just run the archive on {tok} now, I will approve later.",
    ),
}

_NOTE_TEXTS = ("follow up next week", "customer confirmed by phone", "awaiting vendor reply")

_FINAL: dict[str, tuple[str, ...]] = {
    "lookup": (
        "Record {title} is open.",
        "The record {title} has status open.",
        "{title} is currently open.",
    ),
    "note": (
        "I added the note to {title}.",
        "The note is now attached to {title}.",
        "Done: the note was logged on {title}.",
    ),
    "note_retry": (
        "The note was recorded on {title} exactly once; the retry returned the same note.",
        "One note was created on {title}; resending it did not duplicate it.",
        "Retry was safe: {title} has a single copy of the note.",
    ),
    "approval_approved": (
        "Approval was granted and {title} is now archived.",
        "After approval, I executed the archive on {title}.",
        "{title} has been archived once approval came through.",
    ),
    "approval_denied": (
        "Approval for {title} was denied, so I did not archive it.",
        "The approver denied the request; {title} remains open.",
        "I could not archive {title}: the approval was denied.",
    ),
    "cross_subject": (
        "I cannot open that record: it belongs to another user, so access was refused.",
        "Access to that record is refused because you do not own it.",
        "That record is not yours, so I will not read it.",
    ),
    "execute_pending": (
        "I cannot archive {title} yet: the approval is still pending, so execution was refused.",
        "The action on {title} needs an approver's decision first; it has not run.",
        "Execution refused for {title} until the approval is granted.",
    ),
}


def _template_id(kind: str, variant: int) -> str:
    return f"{_TEMPLATE_PREFIX}{kind}-v{variant}"


# ---------------------------------------------------------------------------
# Execution against the real backend
# ---------------------------------------------------------------------------

_REF_PREFIX = {"record_id": "rec", "note_id": "note", "approval_id": "appr"}


class _Refs:
    """Bidirectional real-id <-> symbolic ref map for one trace."""

    def __init__(self) -> None:
        self._real_to_ref: dict[str, str] = {}
        self._ref_to_real: dict[str, str] = {}
        self._counts: dict[str, int] = {}

    def register(self, key: str, real: str) -> str:
        known = self._real_to_ref.get(real)
        if known is not None:
            return known
        prefix = _REF_PREFIX[key]
        ref = f"{prefix}-{self._counts.get(prefix, 0)}"
        self._counts[prefix] = self._counts.get(prefix, 0) + 1
        self._real_to_ref[real] = ref
        self._ref_to_real[ref] = real
        return ref

    def real(self, ref: str) -> str:
        return self._ref_to_real.get(ref, ref)

    def normalize(self, value: Any) -> Any:
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for k, v in value.items():
                if k in _REF_PREFIX and isinstance(v, str):
                    out[k] = self.register(k, v)
                else:
                    out[k] = self.normalize(v)
            return out
        if isinstance(value, list):
            return [self.normalize(v) for v in value]
        return value

    def resolve_args(self, args: dict[str, Any]) -> dict[str, Any]:
        return {k: (self.real(v) if k in _REF_PREFIX else v) for k, v in args.items()}


def _seed_for(title: str, foreign_title: str) -> ToolEnvironmentSeed:
    return ToolEnvironmentSeed(
        tenants=(_TENANT,),
        subjects_by_tenant={_TENANT: (_OWNER, _OTHER)},
        records=((_TENANT, _OWNER, title), (_TENANT, _OTHER, foreign_title)),
    )


def _foreign_title(title: str) -> str:
    return f"Other {title}"


@dataclass(frozen=True)
class _Say:
    role: str
    content: str


@dataclass(frozen=True)
class _Call:
    tool: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class _Approver:
    decision: str
    approval_ref: str


_PlanItem = _Say | _Call | _Approver


def _execute_plan(plan: list[_PlanItem], *, title: str) -> list[dict[str, Any]]:
    """Run an agent plan on a fresh real environment and return the recorded
    steps (results normalized to symbolic refs)."""
    env = ToolEnvironment(seed=_seed_for(title, _foreign_title(title)))
    refs = _Refs()
    refs.register(
        "record_id",
        env.record_id_for(tenant_id=_TENANT, owner_subject_id=_OWNER, title=title),
    )
    refs.register(
        "record_id",
        env.record_id_for(tenant_id=_TENANT, owner_subject_id=_OTHER, title=_foreign_title(title)),
    )
    steps: list[dict[str, Any]] = []
    for item in plan:
        if isinstance(item, _Say):
            steps.append({"type": "message", "role": item.role, "content": item.content})
        elif isinstance(item, _Call):
            steps.append(
                {"type": "tool_call", "tool": item.tool, "arguments": dict(item.arguments)}
            )
            try:
                result = env.dispatch(
                    item.tool,
                    tenant_id=_TENANT,
                    subject_id=_OWNER,
                    arguments=refs.resolve_args(item.arguments),
                )
            except Exception as exc:  # noqa: BLE001 - recorded as typed, content-free error
                steps.append(
                    {"type": "tool_error", "tool": item.tool, "error_type": type(exc).__name__}
                )
            else:
                steps.append(
                    {"type": "tool_result", "tool": item.tool, "result": refs.normalize(result)}
                )
        else:
            env.server.decide_approval(
                tenant_id=_TENANT,
                approver_subject_id=_APPROVER,
                approval_id=refs.real(item.approval_ref),
                decision=item.decision,
            )
            steps.append(
                {
                    "type": "approver_decision",
                    "approval_id": item.approval_ref,
                    "decision": item.decision,
                }
            )
    return steps


_REQUEST_ARCHIVE = _Call("request_approval", {"record_id": "rec-0", "action": "archive_record"})


def _search(tok: str) -> _Call:
    return _Call("search_records", {"query": tok})


def _get_own() -> _Call:
    return _Call("get_record", {"record_id": "rec-0"})


def _note_call(variant: int, idem: str) -> _Call:
    return _Call(
        "create_note",
        {"record_id": "rec-0", "text": _NOTE_TEXTS[variant], "idempotency_key": idem},
    )


def _execute_call(idem: str) -> _Call:
    return _Call("execute_approved_action", {"approval_id": "appr-0", "idempotency_key": idem})


def _body_lookup(tok: str, variant: int, idem: str) -> list[_PlanItem]:
    return [_search(tok), _get_own()]


def _body_note(tok: str, variant: int, idem: str) -> list[_PlanItem]:
    return [_search(tok), _get_own(), _note_call(variant, idem)]


def _body_note_retry(tok: str, variant: int, idem: str) -> list[_PlanItem]:
    return [_search(tok), _note_call(variant, idem), _note_call(variant, idem)]


def _body_approved(tok: str, variant: int, idem: str) -> list[_PlanItem]:
    return [_search(tok), _REQUEST_ARCHIVE, _Approver("approved", "appr-0"), _execute_call(idem)]


def _body_denied(tok: str, variant: int, idem: str) -> list[_PlanItem]:
    return [_search(tok), _REQUEST_ARCHIVE, _Approver("denied", "appr-0"), _execute_call(idem)]


def _body_cross_subject(tok: str, variant: int, idem: str) -> list[_PlanItem]:
    return [_Call("get_record", {"record_id": "rec-1"})]


def _body_pending(tok: str, variant: int, idem: str) -> list[_PlanItem]:
    return [_search(tok), _REQUEST_ARCHIVE, _execute_call(idem)]


_BODIES = {
    "lookup": _body_lookup,
    "note": _body_note,
    "note_retry": _body_note_retry,
    "approval_approved": _body_approved,
    "approval_denied": _body_denied,
    "cross_subject": _body_cross_subject,
    "execute_pending": _body_pending,
}


def _plan_for(kind: str, *, title: str, tok: str, variant: int, idem: str) -> list[_PlanItem]:
    user = _USER_PHRASES[kind][variant].format(tok=tok, note=_NOTE_TEXTS[variant], foreign="rec-1")
    final = _FINAL[kind][variant].format(title=title)
    return [_Say("user", user), *_BODIES[kind](tok, variant, idem), _Say("assistant", final)]


def _plan_from_steps(steps: tuple[dict[str, Any], ...]) -> list[_PlanItem]:
    """Recover the agent-side actions from a recorded trace. Recorded
    results/errors are dropped; a step type this function does not know is
    dropped too, and the replay comparison then refuses the trace because the
    replayed steps no longer equal the recorded ones."""
    plan: list[_PlanItem] = []
    for step in steps:
        t = step.get("type")
        if t == "message":
            plan.append(_Say(step["role"], step["content"]))
        elif t == "tool_call":
            plan.append(_Call(step["tool"], dict(step["arguments"])))
        elif t == "approver_decision":
            plan.append(_Approver(step["decision"], step["approval_id"]))
    return plan


def _results(steps: list[dict[str, Any]], tool: str) -> list[dict[str, Any]]:
    return [s["result"] for s in steps if s["type"] == "tool_result" and s["tool"] == tool]


def _error_types(steps: list[dict[str, Any]], tool: str) -> list[str]:
    return [s["error_type"] for s in steps if s["type"] == "tool_error" and s["tool"] == tool]


def _holds_record_read(steps: list[dict[str, Any]]) -> bool:
    return any(r["status"] == "open" for r in _results(steps, "get_record"))


def _holds_note_created(steps: list[dict[str, Any]]) -> bool:
    return len(_results(steps, "create_note")) >= 1


def _holds_note_once(steps: list[dict[str, Any]]) -> bool:
    ids = [r["note_id"] for r in _results(steps, "create_note")]
    return len(ids) == 2 and ids[0] == ids[1]


def _holds_executed(steps: list[dict[str, Any]]) -> bool:
    return any(r["status"] == "executed" for r in _results(steps, "execute_approved_action"))


def _holds_action_refused(steps: list[dict[str, Any]]) -> bool:
    refused = "ReferenceDenialError" in _error_types(steps, "execute_approved_action")
    executed = len(_results(steps, "execute_approved_action")) > 0
    return refused and not executed


def _holds_access_refused(steps: list[dict[str, Any]]) -> bool:
    refused = "ReferenceAuthorizationError" in _error_types(steps, "get_record")
    read = len(_results(steps, "get_record")) > 0
    return refused and not read


_OUTCOME_CHECKS = {
    "record_read": _holds_record_read,
    "note_created": _holds_note_created,
    "note_created_once": _holds_note_once,
    "action_executed": _holds_executed,
    "action_refused_denied": _holds_action_refused,
    "action_refused_pending": _holds_action_refused,
    "access_refused": _holds_access_refused,
}


def _outcome_holds(outcome: str, steps: list[dict[str, Any]]) -> bool:
    check = _OUTCOME_CHECKS.get(outcome)
    return check is not None and check(steps)


def _trace_hash_source(trace: TrainingTrace) -> str:
    return json.dumps(trace.to_dict(), sort_keys=True, separators=(",", ":"))


def generate_training_traces(*, count: int = 1200) -> tuple[TrainingTrace, ...]:
    """Deterministically generate `count` synthetic traces, each executed on
    its own disposable real backend, one independent entity group per trace."""
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise TrainingDatasetError("count must be a positive integer")
    traces: list[TrainingTrace] = []
    for i in range(count):
        kind, critical, outcome = _KINDS[i % len(_KINDS)]
        variant = (i // len(_KINDS)) % 3
        adj = _ADJECTIVES[i % len(_ADJECTIVES)]
        noun = _NOUNS[(i // len(_ADJECTIVES)) % len(_NOUNS)]
        tok = f"TRN-{i:04d}"
        title = f"{adj} {noun} {tok}"
        plan = _plan_for(kind, title=title, tok=tok, variant=variant, idem=f"idem-{i:04d}")
        steps = _execute_plan(plan, title=title)
        trace = TrainingTrace(
            trace_id=f"train-{i:04d}",
            group_id=f"{_GROUP_PREFIX}{i:04d}",
            template_id=_template_id(kind, variant),
            kind=kind,
            critical=critical,
            entity_title=title,
            expected_outcome=outcome,
            steps=tuple(steps),
            provenance={
                "origin": _ORIGIN,
                "producer": GENERATOR_ID,
                "label": "synthetic",
                "index": i,
            },
        )
        traces.append(trace)
    return tuple(traces)


def verify_trace_replay(trace: TrainingTrace) -> None:
    """Re-execute the trace's recorded agent actions on a fresh real
    environment; raise unless every recorded result and the declared
    outcome match."""
    if not isinstance(trace, TrainingTrace):
        raise TraceVerificationError("not a TrainingTrace")
    if not isinstance(trace.entity_title, str) or not trace.entity_title:
        raise TraceVerificationError("entity_title must be a non-empty string")
    plan = _plan_from_steps(trace.steps)
    replayed = _execute_plan(plan, title=trace.entity_title)
    if replayed != [dict(s) for s in trace.steps]:
        raise TraceVerificationError(
            f"{trace.trace_id}: recorded results do not replay against the real backend"
        )
    allowed_final = {f.format(title=trace.entity_title) for f in _FINAL.get(trace.kind, ())}
    if trace.final_answer() not in allowed_final:
        raise TraceVerificationError(
            f"{trace.trace_id}: final answer does not match the verified outcome for {trace.kind!r}"
        )
    if not _outcome_holds(trace.expected_outcome, replayed):
        raise TraceVerificationError(
            f"{trace.trace_id}: declared outcome {trace.expected_outcome!r} does not hold"
        )


# ---------------------------------------------------------------------------
# Safety scans and the review-gated builder
# ---------------------------------------------------------------------------


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (_strings(k) + _strings(v))]
    if isinstance(value, list | tuple):
        return [s for v in value for s in _strings(v)]
    return []


def _scan_secrets(trace: TrainingTrace) -> None:
    for text in _strings(trace.to_dict()):
        for pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                raise SecretInTraceError(f"{trace.trace_id}: secret-shaped content refused")


def _norm(value: str) -> str:
    return value.strip().casefold()


def _check_reserved(trace: TrainingTrace, reserved: ReservedFinalSuite) -> None:
    """Case/whitespace-insensitive on both sides, so a re-cased copy of a
    sealed id cannot slip past the held-out guard."""
    answer_hash = _norm(hashlib.sha256(trace.final_answer().encode("utf-8")).hexdigest())
    overlaps = (
        (_norm(trace.group_id), {_norm(x) for x in reserved.group_ids}),
        (_norm(trace.template_id), {_norm(x) for x in reserved.template_ids}),
        (_norm(trace.entity_title), {_norm(x) for x in reserved.entity_titles}),
        (answer_hash, {_norm(x) for x in reserved.answer_sha256s}),
    )
    if any(probe in sealed for probe, sealed in overlaps):
        raise HeldOutLeakageError(f"{trace.trace_id}: overlaps the sealed final suite")


def _validate_trace_shape(trace: Any) -> TrainingTrace:
    if not isinstance(trace, TrainingTrace):
        raise TrainingDatasetError("traces must be TrainingTrace instances")
    if trace.purpose not in (_PURPOSE,):
        raise TrainingDatasetError(f"{trace.trace_id}: purpose must be 'training'")
    if (
        not isinstance(trace.provenance, dict)
        or trace.provenance.get("origin") not in (_ORIGIN,)
        or trace.provenance.get("label") != "synthetic"
        or not isinstance(trace.provenance.get("producer"), str)
        or not trace.provenance["producer"]
    ):
        raise TrainingDatasetError(
            f"{trace.trace_id}: provenance must be synthetic generator output"
        )
    if not trace.template_id.startswith(_TEMPLATE_PREFIX) or not trace.group_id.startswith(
        _GROUP_PREFIX
    ):
        raise TrainingDatasetError(
            f"{trace.trace_id}: group/template outside the training namespace"
        )
    return trace


def _validate_review(review: Any, producers: dict[str, str]) -> ReviewDecision:
    if not isinstance(review, ReviewDecision):
        raise ReviewIndependenceError("reviews must be ReviewDecision instances")
    for name in ("trace_id", "reviewer_id", "decision"):
        if not isinstance(getattr(review, name), str) or not getattr(review, name):
            raise ReviewIndependenceError(f"review.{name} must be a non-empty string")
    if review.decision not in _DECISIONS:
        raise ReviewIndependenceError(f"unknown review decision: {review.decision!r}")
    producer = producers.get(review.trace_id)
    if producer is None:
        raise ReviewIndependenceError(f"review for unknown trace: {review.trace_id!r}")
    if _norm(review.reviewer_id).startswith("generator:") or _norm(review.reviewer_id) == _norm(
        producer
    ):
        raise ReviewIndependenceError(f"{review.trace_id}: reviewer is not independent of producer")
    return review


def build_training_dataset(
    traces: tuple[TrainingTrace, ...] | list[TrainingTrace],
    reviews: tuple[ReviewDecision, ...] | list[ReviewDecision] = (),
    *,
    reserved: ReservedFinalSuite | None = None,
) -> TrainingDataset:
    """Verify every trace, then admit only independently approved ones."""
    if not isinstance(traces, tuple | list) or not isinstance(reviews, tuple | list):
        raise TrainingDatasetError("traces and reviews must be sequences")
    reserved = reserved or ReservedFinalSuite()
    by_id: dict[str, TrainingTrace] = {}
    seen_ids: set[str] = set()
    for raw in traces:
        trace = _validate_trace_shape(raw)
        if _norm(trace.trace_id) in seen_ids:
            raise TrainingDatasetError(f"duplicate trace_id: {trace.trace_id!r}")
        seen_ids.add(_norm(trace.trace_id))
        _scan_secrets(trace)
        _check_reserved(trace, reserved)
        verify_trace_replay(trace)
        by_id[trace.trace_id] = trace

    producers = {tid: t.provenance["producer"] for tid, t in by_id.items()}
    decisions: dict[str, str] = {}
    for raw_review in reviews:
        review = _validate_review(raw_review, producers)
        if decisions.setdefault(review.trace_id, review.decision) != review.decision:
            raise ReviewIndependenceError(f"{review.trace_id}: conflicting review decisions")

    approved = tuple(
        sorted(
            (t for tid, t in by_id.items() if decisions.get(tid) == "approved"),
            key=lambda t: t.trace_id,
        )
    )
    rejected = sum(1 for d in decisions.values() if d == "rejected")
    digest = hashlib.sha256()
    for trace in approved:
        digest.update(_trace_hash_source(trace).encode("utf-8"))
        digest.update(b"\n")
    groups = {t.group_id for t in approved}
    meets = len(approved) >= MIN_APPROVED_TRACES and len(groups) >= MIN_APPROVED_TRACES
    return TrainingDataset(
        traces=approved,
        generated_count=len(by_id),
        approved_count=len(approved),
        pending_count=len(by_id) - len(approved) - rejected,
        rejected_count=rejected,
        group_count=len(groups),
        critical_count=sum(1 for t in approved if t.critical),
        dataset_sha256=digest.hexdigest(),
        meets_floor=meets,
        review_status="independently_approved" if meets else "BLOCKED_EXTERNAL_independent_review",
    )
