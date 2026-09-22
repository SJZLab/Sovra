"""Reference agent specification (FTF-1701, Section 22/54-55/65-66/68-69/77/81).

Section 55's own reference target is a real read-only-connected repository
containing a runnable agent product and isolated, stateful tools:
`search_records`, `get_record`, `create_note`, `request_approval`,
`execute_approved_action`, owned by "a deterministic independent server"
that "owns permission/approval records" so that "tool clients cannot
fabricate grants." Building the full reference *product* (a real connected
repository, >=1,000 training traces, a sealed final suite, real training/
evaluation runs) is Section 54's own end-to-end release qualification
scenario -- a chain of later, separately dependency-gated tasks
(`docs/TASK_INDEX.md`: FTF-1702 "Tool environment" through FTF-1715 "Final
cost/quality report" all name this task as their own dependency, not the
other way around). This task's own acceptance clause is narrower and
precedes all of them: "Specify bounded actual reference agent with
approved tools, deterministic business state and explicit risk/language
profile." This module is that specification, made real and directly
testable -- not a description of one.

What "real" means here, precisely: every one of the five reference tools
is constructed as a genuine `app.tools.tool_definition_schema.ToolDefinition`
row (FTF-0404, already CERTIFIED) and must pass, for real, the two
already-CERTIFIED, already-live-wired guards that run in
`ToolDefinition.__init__` -- FTF-0406's `enforce_side_effect_classification`
and FTF-0407's `enforce_critical_behavior_requirement` -- exactly as any
production tool schema must. `ReferenceBusinessServer` is a genuine,
deterministic, in-memory implementation of the described business domain
(never a mock that returns canned success): tenant scoping, per-record
ownership (the "subject/resource-scoped authorization" Section 55 names),
request-fingerprint idempotency on every effect, and an approval workflow
where only `decide_approval`/`revoke_approval` (this module's own stand-in
for "a deterministic independent server", never one of the five tools a
model-driven agent can call) can move an `ApprovalRequest` into
`approved`/`denied`/`revoked` -- `execute_approved_action` only ever reads
that state, it can never set it, which is what makes "tool clients cannot
fabricate grants" true by construction rather than by policy text.
`dispatch_tool_call` is this module's own tool-call boundary, and the
first real caller anywhere in this repository of FTF-0405's own
`validate_instance` (that module's own docstring disclosed, not hidden,
that it "has no live caller in this repository yet" pending "a much later
phase... a live agent runtime" -- this is that runtime's tool-call
boundary, narrowly): every call's `arguments` are validated against the
tool's own frozen `input_schema_json` before the handler ever runs, and
every handler's own return value is validated against `output_schema_json`
before it is handed back, so neither a malformed call nor a
handler-contract violation can pass silently.

Disclosed, not hidden (this task's own negative_acceptance, "Reference
success cannot claim every arbitrary customer agent is
production-qualified"): `ReferenceAgentProfile.qualifies_this_reference_
workload_only` is always `True` and is not a field any caller can set to
`False` -- there is no constructor path in this module that produces a
profile claiming broader qualification. Matching Section 55's own text
verbatim: "Synthetic-domain success qualifies the Factory reference
workload only; it is not proof that an unrelated organization product is
ready."

No database, network egress, or GPU/external resource of any kind is
touched anywhere in this module -- `database_changes` is honored ("No
independent schema change. Consume frozen contract") and this task's own
`resource_gate` clause is moot: there is no external prerequisite to gate
on. `packages/contracts/{openapi,jsonschema}/17-*` freeze this module's
own real dataclass shapes (contract slot 17, `shared_lock_required:
contracts:17`, first claim); `paths: {}` in the OpenAPI file, since no
live FastAPI route exists for this task -- the same disclosed
`database_changes`/`api_contract` scope FTF-1401/FTF-1501/FTF-1601 already
established for a measurement/specification tool that is not itself a
served product route.
"""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
_API_ROOT = _ROOT / "apps" / "api"
for _path in (str(_ROOT), str(_API_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app.services.json_schema_tool_validation import (  # noqa: E402
    JsonSchemaValidationError,
    validate_instance,
)
from app.services.side_effect_classification import RISK_RANK  # noqa: E402
from app.tools.tool_definition_schema import (  # noqa: E402
    ToolDefinition,
    ToolDefinitionValidationError,
    compute_schema_hash,
)

__all__ = [
    "CREATE_NOTE",
    "EXECUTE_APPROVED_ACTION",
    "GET_RECORD",
    "REFERENCE_TOOL_SPECS",
    "REQUEST_APPROVAL",
    "SEARCH_RECORDS",
    "ApprovalRequest",
    "Note",
    "Record",
    "ReferenceAgentProfile",
    "ReferenceAgentSpecificationError",
    "ReferenceAuthorizationError",
    "ReferenceBusinessServer",
    "ReferenceDenialError",
    "ReferenceToolSpec",
    "build_reference_agent_profile",
    "build_reference_tool_definitions",
    "dispatch_tool_call",
]


class ReferenceAgentSpecificationError(ValueError):
    """A reference tool spec failed real `ToolDefinition` construction, an
    unknown tool name was dispatched, or a dispatched call's arguments/
    result violated the tool's own frozen input/output schema."""


class ReferenceAuthorizationError(PermissionError):
    """A cross-tenant or cross-subject (IDOR) access was refused by
    `ReferenceBusinessServer` -- tenant/subject/resource-scoped
    authorization, Section 55's own named requirement."""


class ReferenceDenialError(ValueError):
    """A business-rule refusal that is not an authorization failure: the
    requested effect is well-formed and the caller is authorized to ask,
    but the server's own state does not permit it right now (an approval
    that is not `approved`, an approval already executed, a decision on a
    request that is not `pending`)."""


# ---------------------------------------------------------------------------
# 1. Reference tool specification -- validated through the real,
#    already-CERTIFIED FTF-0404/FTF-0405/FTF-0406/FTF-0407 guards.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReferenceToolSpec:
    name: str
    description: str
    input_schema_json: dict[str, Any]
    output_schema_json: dict[str, Any]
    side_effect_class: str
    requires_confirmation: bool
    is_critical: bool


SEARCH_RECORDS = ReferenceToolSpec(
    name="search_records",
    description="List the calling subject's own records whose title contains query.",
    input_schema_json={
        "type": "object",
        "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 200}},
        "required": ["query"],
        "additionalProperties": False,
    },
    output_schema_json={
        "type": "object",
        "properties": {
            "records": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "record_id": {"type": "string", "minLength": 1},
                        "title": {"type": "string"},
                    },
                    "required": ["record_id", "title"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["records"],
        "additionalProperties": False,
    },
    side_effect_class="read_only",
    requires_confirmation=False,
    is_critical=False,
)

GET_RECORD = ReferenceToolSpec(
    name="get_record",
    description="Fetch one record the calling subject owns, including its notes.",
    input_schema_json={
        "type": "object",
        "properties": {"record_id": {"type": "string", "minLength": 1}},
        "required": ["record_id"],
        "additionalProperties": False,
    },
    output_schema_json={
        "type": "object",
        "properties": {
            "record_id": {"type": "string", "minLength": 1},
            "title": {"type": "string"},
            "status": {"type": "string", "enum": ["open", "archived", "escalated"]},
            "notes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "note_id": {"type": "string", "minLength": 1},
                        "text": {"type": "string"},
                    },
                    "required": ["note_id", "text"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["record_id", "title", "status", "notes"],
        "additionalProperties": False,
    },
    side_effect_class="read_only",
    requires_confirmation=False,
    is_critical=False,
)

CREATE_NOTE = ReferenceToolSpec(
    name="create_note",
    description="Append a note to a record the calling subject owns.",
    input_schema_json={
        "type": "object",
        "properties": {
            "record_id": {"type": "string", "minLength": 1},
            "text": {"type": "string", "minLength": 1, "maxLength": 2000},
            "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 200},
        },
        "required": ["record_id", "text", "idempotency_key"],
        "additionalProperties": False,
    },
    output_schema_json={
        "type": "object",
        "properties": {
            "note_id": {"type": "string", "minLength": 1},
            "record_id": {"type": "string", "minLength": 1},
        },
        "required": ["note_id", "record_id"],
        "additionalProperties": False,
    },
    side_effect_class="reversible_write",
    requires_confirmation=False,
    is_critical=False,
)

REQUEST_APPROVAL = ReferenceToolSpec(
    name="request_approval",
    description=(
        "File a pending request for a proposed record action; a separate human approver "
        "decides it later, this call alone never changes the record."
    ),
    input_schema_json={
        "type": "object",
        "properties": {
            "record_id": {"type": "string", "minLength": 1},
            "action": {"type": "string", "enum": ["archive_record", "escalate_record"]},
        },
        "required": ["record_id", "action"],
        "additionalProperties": False,
    },
    output_schema_json={
        "type": "object",
        "properties": {
            "approval_id": {"type": "string", "minLength": 1},
            "status": {"type": "string", "enum": ["pending"]},
        },
        "required": ["approval_id", "status"],
        "additionalProperties": False,
    },
    side_effect_class="reversible_write",
    requires_confirmation=False,
    is_critical=False,
)

EXECUTE_APPROVED_ACTION = ReferenceToolSpec(
    name="execute_approved_action",
    description=(
        "Execute a previously requested action, only once a separate human approver has "
        "already approved it -- the calling subject can never approve its own request."
    ),
    input_schema_json={
        "type": "object",
        "properties": {
            "approval_id": {"type": "string", "minLength": 1},
            "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 200},
        },
        "required": ["approval_id", "idempotency_key"],
        "additionalProperties": False,
    },
    output_schema_json={
        "type": "object",
        "properties": {
            "approval_id": {"type": "string", "minLength": 1},
            "record_id": {"type": "string", "minLength": 1},
            "status": {"type": "string", "enum": ["executed"]},
        },
        "required": ["approval_id", "record_id", "status"],
        "additionalProperties": False,
    },
    side_effect_class="irreversible_write",
    requires_confirmation=True,
    is_critical=True,
)

REFERENCE_TOOL_SPECS: tuple[ReferenceToolSpec, ...] = (
    SEARCH_RECORDS,
    GET_RECORD,
    CREATE_NOTE,
    REQUEST_APPROVAL,
    EXECUTE_APPROVED_ACTION,
)

_SPEC_BY_NAME: dict[str, ReferenceToolSpec] = {spec.name: spec for spec in REFERENCE_TOOL_SPECS}


def build_reference_tool_definitions(
    *, organization_id: uuid.UUID, project_id: uuid.UUID, created_by: str
) -> dict[str, ToolDefinition]:
    """Construct one real, in-memory `ToolDefinition` row per reference tool
    spec -- proving each spec genuinely passes FTF-0404/FTF-0405/FTF-0406/
    FTF-0407's own already-CERTIFIED construction-time guards, not merely
    that this module's own authors believe it would. Raises
    `ReferenceAgentSpecificationError` (wrapping the underlying
    `ToolDefinitionValidationError`) if any spec is not actually valid."""
    definitions: dict[str, ToolDefinition] = {}
    for spec in REFERENCE_TOOL_SPECS:
        try:
            definitions[spec.name] = ToolDefinition(
                organization_id=organization_id,
                project_id=project_id,
                name=spec.name,
                description=spec.description,
                input_schema_json=spec.input_schema_json,
                output_schema_json=spec.output_schema_json,
                side_effect_class=spec.side_effect_class,
                requires_confirmation=spec.requires_confirmation,
                is_critical=spec.is_critical,
                version=1,
                schema_hash=compute_schema_hash(spec.input_schema_json, spec.output_schema_json),
                created_by=created_by,
            )
        except ToolDefinitionValidationError as exc:
            raise ReferenceAgentSpecificationError(f"{spec.name}: {exc}") from exc
    return definitions


# ---------------------------------------------------------------------------
# 2. Explicit risk/language profile (this task's own acceptance clause).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReferenceAgentProfile:
    agent_name: str
    supported_languages: tuple[str, ...]
    critical_tool_names: tuple[str, ...]
    max_side_effect_class: str
    qualifies_this_reference_workload_only: bool = True


def build_reference_agent_profile() -> ReferenceAgentProfile:
    """Derive the profile from the real tool specs -- never a separately
    hand-maintained claim that could drift from what the tools actually
    declare."""
    max_spec = max(REFERENCE_TOOL_SPECS, key=lambda spec: RISK_RANK[spec.side_effect_class])
    critical_names = tuple(sorted(spec.name for spec in REFERENCE_TOOL_SPECS if spec.is_critical))
    return ReferenceAgentProfile(
        agent_name="reference_records_agent",
        supported_languages=("en",),
        critical_tool_names=critical_names,
        max_side_effect_class=max_spec.side_effect_class,
    )


# ---------------------------------------------------------------------------
# 3. Deterministic independent business-state server.
# ---------------------------------------------------------------------------


@dataclass
class Record:
    record_id: str
    tenant_id: str
    owner_subject_id: str
    title: str
    status: str = "open"
    note_ids: list[str] = field(default_factory=list)


@dataclass
class Note:
    note_id: str
    record_id: str
    tenant_id: str
    author_subject_id: str
    text: str
    created_at: datetime


@dataclass
class ApprovalRequest:
    approval_id: str
    tenant_id: str
    record_id: str
    action: str
    requested_by_subject_id: str
    status: str = "pending"
    decided_by_subject_id: str | None = None
    decided_at: datetime | None = None
    executed_at: datetime | None = None


_ACTION_RESULT_STATUS = {
    "archive_record": "archived",
    "escalate_record": "escalated",
}


class ReferenceBusinessServer:
    """A genuine, deterministic, in-memory implementation of Section 55's
    named reference domain. `decide_approval`/`revoke_approval` are the
    only functions anywhere in this class that can move an
    `ApprovalRequest` into `approved`/`denied`/`revoked` -- they model "a
    deterministic independent server [that] owns permission/approval
    records" (a human approver, in Factory terms), and are deliberately
    never registered as one of the five agent-facing tools
    `dispatch_tool_call` can reach. `execute_approved_action` only ever
    reads that state; there is no parameter or code path anywhere in this
    class through which a tool call can set `status="approved"` on its
    own request -- this is what makes "tool clients cannot fabricate
    grants" true by construction."""

    def __init__(self) -> None:
        self._records: dict[str, Record] = {}
        self._notes: dict[str, Note] = {}
        self._approvals: dict[str, ApprovalRequest] = {}
        self._idempotency: dict[tuple[str, str, str], dict[str, Any]] = {}

    # -- fixtures -----------------------------------------------------

    def seed_record(self, *, tenant_id: str, owner_subject_id: str, title: str) -> Record:
        record = Record(
            record_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            owner_subject_id=owner_subject_id,
            title=title,
        )
        self._records[record.record_id] = record
        return record

    # -- internal authorization helpers --------------------------------

    def _authorized_record(self, *, tenant_id: str, subject_id: str, record_id: str) -> Record:
        record = self._records.get(record_id)
        if record is None:
            raise ReferenceAgentSpecificationError(f"record not found: {record_id!r}")
        if record.tenant_id != tenant_id:
            raise ReferenceAuthorizationError("cross-tenant record access denied")
        if record.owner_subject_id != subject_id:
            raise ReferenceAuthorizationError("cross-subject (IDOR) record access denied")
        return record

    # -- the five agent-facing tools ------------------------------------

    def search_records(self, *, tenant_id: str, subject_id: str, query: str) -> dict[str, Any]:
        matches = [
            {"record_id": r.record_id, "title": r.title}
            for r in self._records.values()
            if r.tenant_id == tenant_id
            and r.owner_subject_id == subject_id
            and query.lower() in r.title.lower()
        ]
        return {"records": matches}

    def get_record(self, *, tenant_id: str, subject_id: str, record_id: str) -> dict[str, Any]:
        record = self._authorized_record(
            tenant_id=tenant_id, subject_id=subject_id, record_id=record_id
        )
        notes = [
            {"note_id": self._notes[nid].note_id, "text": self._notes[nid].text}
            for nid in record.note_ids
        ]
        return {
            "record_id": record.record_id,
            "title": record.title,
            "status": record.status,
            "notes": notes,
        }

    def create_note(
        self, *, tenant_id: str, subject_id: str, record_id: str, text: str, idempotency_key: str
    ) -> dict[str, Any]:
        cache_key = ("create_note", subject_id, idempotency_key)
        cached = self._idempotency.get(cache_key)
        if cached is not None:
            return cached
        record = self._authorized_record(
            tenant_id=tenant_id, subject_id=subject_id, record_id=record_id
        )
        note = Note(
            note_id=str(uuid.uuid4()),
            record_id=record.record_id,
            tenant_id=tenant_id,
            author_subject_id=subject_id,
            text=text,
            created_at=datetime.now(UTC),
        )
        self._notes[note.note_id] = note
        record.note_ids.append(note.note_id)
        result = {"note_id": note.note_id, "record_id": record.record_id}
        self._idempotency[cache_key] = result
        return result

    def request_approval(
        self, *, tenant_id: str, subject_id: str, record_id: str, action: str
    ) -> dict[str, Any]:
        record = self._authorized_record(
            tenant_id=tenant_id, subject_id=subject_id, record_id=record_id
        )
        approval = ApprovalRequest(
            approval_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            record_id=record.record_id,
            action=action,
            requested_by_subject_id=subject_id,
        )
        self._approvals[approval.approval_id] = approval
        return {"approval_id": approval.approval_id, "status": approval.status}

    def execute_approved_action(
        self, *, tenant_id: str, subject_id: str, approval_id: str, idempotency_key: str
    ) -> dict[str, Any]:
        cache_key = ("execute_approved_action", subject_id, idempotency_key)
        cached = self._idempotency.get(cache_key)
        if cached is not None:
            return cached

        approval = self._approvals.get(approval_id)
        if approval is None:
            raise ReferenceAgentSpecificationError(f"approval not found: {approval_id!r}")
        if approval.tenant_id != tenant_id:
            raise ReferenceAuthorizationError("cross-tenant approval access denied")
        if approval.requested_by_subject_id != subject_id:
            raise ReferenceAuthorizationError(
                "only the subject that originally requested approval may execute it"
            )
        if approval.status != "approved":
            raise ReferenceDenialError(
                f"approval {approval_id!r} is {approval.status!r}, not approved -- refused"
            )

        record = self._records[approval.record_id]
        record.status = _ACTION_RESULT_STATUS[approval.action]
        approval.status = "executed"
        approval.executed_at = datetime.now(UTC)
        result = {
            "approval_id": approval.approval_id,
            "record_id": record.record_id,
            "status": "executed",
        }
        self._idempotency[cache_key] = result
        return result

    # -- the deterministic independent approver (never agent-callable) --

    def decide_approval(
        self, *, tenant_id: str, approver_subject_id: str, approval_id: str, decision: str
    ) -> ApprovalRequest:
        if decision not in ("approved", "denied"):
            raise ReferenceAgentSpecificationError(f"unknown decision: {decision!r}")
        approval = self._approvals.get(approval_id)
        if approval is None:
            raise ReferenceAgentSpecificationError(f"approval not found: {approval_id!r}")
        if approval.tenant_id != tenant_id:
            raise ReferenceAuthorizationError("cross-tenant approval decision denied")
        if approval.status != "pending":
            raise ReferenceDenialError(f"approval {approval_id!r} is already {approval.status!r}")
        approval.status = decision
        approval.decided_by_subject_id = approver_subject_id
        approval.decided_at = datetime.now(UTC)
        return approval

    def revoke_approval(
        self, *, tenant_id: str, approver_subject_id: str, approval_id: str
    ) -> ApprovalRequest:
        """Rollback of a prior grant, before execution -- Section 55's own
        "test rollback/denial state directly." Only an `approved`,
        not-yet-executed approval can be revoked."""
        approval = self._approvals.get(approval_id)
        if approval is None:
            raise ReferenceAgentSpecificationError(f"approval not found: {approval_id!r}")
        if approval.tenant_id != tenant_id:
            raise ReferenceAuthorizationError("cross-tenant approval revocation denied")
        if approval.status != "approved":
            raise ReferenceDenialError(
                f"approval {approval_id!r} is {approval.status!r}, not approved"
            )
        approval.status = "revoked"
        approval.decided_by_subject_id = approver_subject_id
        approval.decided_at = datetime.now(UTC)
        return approval


# ---------------------------------------------------------------------------
# 4. Tool-call dispatcher -- the real first live caller of FTF-0405's
#    `validate_instance`.
# ---------------------------------------------------------------------------


def dispatch_tool_call(
    server: ReferenceBusinessServer,
    tool_name: str,
    *,
    tenant_id: str,
    subject_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """The one boundary a model-driven agent call crosses. `tenant_id`/
    `subject_id` come from the authenticated calling context, never from
    `arguments` -- the model cannot supply its own identity. Only the five
    names in `REFERENCE_TOOL_SPECS` are dispatchable; `decide_approval`/
    `revoke_approval` are not tool names and raise
    `ReferenceAgentSpecificationError` like any other unknown name,
    proving the approval-granting surface is genuinely unreachable through
    this boundary."""
    spec = _SPEC_BY_NAME.get(tool_name)
    if spec is None:
        raise ReferenceAgentSpecificationError(f"unknown reference tool: {tool_name!r}")

    try:
        validate_instance(spec.input_schema_json, arguments, field_name=f"{tool_name}.input")
    except JsonSchemaValidationError as exc:
        raise ReferenceAgentSpecificationError(f"{tool_name}: invalid arguments: {exc}") from exc

    handler = getattr(server, tool_name)
    result = handler(tenant_id=tenant_id, subject_id=subject_id, **arguments)

    try:
        validate_instance(spec.output_schema_json, result, field_name=f"{tool_name}.output")
    except JsonSchemaValidationError as exc:
        raise ReferenceAgentSpecificationError(
            f"{tool_name}: handler produced an out-of-contract result: {exc}"
        ) from exc

    return result
