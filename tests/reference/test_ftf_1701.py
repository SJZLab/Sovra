#!/usr/bin/env python3
"""FTF-1701 -- Reference agent specification (no-DB unit/fault tests).

Runnable without Postgres, GPU, or network egress -- this task's own
`resource_gate` clause is moot, since nothing here touches an external
prerequisite. This suite proves, for real:

1. Every one of the five reference tool specs constructs a genuine
   `ToolDefinition` row and passes FTF-0404/FTF-0406/FTF-0407's own
   already-CERTIFIED construction-time guards (no regression, and no
   silently-invalid spec).
2. `build_reference_agent_profile` derives a real risk/language profile
   from those specs -- `execute_approved_action` is the sole critical
   tool, `irreversible_write` is the max side-effect class, and
   `qualifies_this_reference_workload_only` is always `True` (this task's
   own negative_acceptance: reference success cannot claim every
   arbitrary customer agent is production-qualified).
3. Tenant/subject/resource-scoped authorization: cross-tenant and
   cross-subject (IDOR) access are both refused by
   `ReferenceAuthorizationError`, for real, on a real in-memory record.
4. Idempotency: repeating `create_note`/`execute_approved_action` with the
   same idempotency key never creates a second effect.
5. Denial/rollback state, tested directly (Section 55's own named
   requirement): `execute_approved_action` refuses a pending, denied, or
   revoked approval; `revoke_approval` models a genuine rollback of a
   prior grant before execution.
6. "Tool clients cannot fabricate grants": the approval-granting surface
   (`decide_approval`/`revoke_approval`) is not a dispatchable tool name --
   `dispatch_tool_call` refuses it exactly like any other unknown name --
   and only the original requester may execute their own approved action.
7. `dispatch_tool_call` is a real live caller of FTF-0405's own
   `validate_instance`: malformed arguments are refused before the
   handler ever runs, and an out-of-contract handler result is refused
   before it is ever returned.
8. A full five-tool happy-path scenario through the real dispatcher.
9. Acceptance clause 1: the frozen `packages/contracts/{openapi,jsonschema}
   /17-*` files parse, declare zero paths (no live route this task owns),
   and real `ReferenceToolSpec`/`ReferenceAgentProfile`/approval-record
   instances genuinely validate against them.

Run directly: python3 tests/reference/test_ftf_1701.py
"""

from __future__ import annotations

import dataclasses
import json
import sys
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from evals.reference.reference_agent_specification import (  # noqa: E402
    EXECUTE_APPROVED_ACTION,
    REFERENCE_TOOL_SPECS,
    ReferenceAgentSpecificationError,
    ReferenceAuthorizationError,
    ReferenceBusinessServer,
    ReferenceDenialError,
    build_reference_agent_profile,
    build_reference_tool_definitions,
    dispatch_tool_call,
)

JSONSCHEMA_PATH = ROOT / "packages" / "contracts" / "jsonschema" / "17-domain.schema.json"
OPENAPI_PATH = ROOT / "packages" / "contracts" / "openapi" / "17-interface.yaml"

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _raises(exc_type: type[BaseException], fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except exc_type:
        return True


ORG_ID = uuid.uuid4()
PROJECT_ID = uuid.uuid4()


def test_all_reference_tool_specs_construct_real_tool_definitions() -> None:
    definitions = build_reference_tool_definitions(
        organization_id=ORG_ID, project_id=PROJECT_ID, created_by="reference-spec-tests"
    )
    check(
        "FTF-1701",
        "all five reference tool specs pass FTF-0404/FTF-0406/FTF-0407's own construction guards",
        set(definitions) == {spec.name for spec in REFERENCE_TOOL_SPECS},
    )
    check(
        "FTF-1701",
        "execute_approved_action resolves irreversible_write/critical/confirmed together",
        definitions["execute_approved_action"].side_effect_class == "irreversible_write"
        and definitions["execute_approved_action"].is_critical is True
        and definitions["execute_approved_action"].requires_confirmation is True,
    )


def test_profile_derives_from_the_real_specs() -> None:
    profile = build_reference_agent_profile()
    check(
        "FTF-1701",
        "execute_approved_action is the sole critical tool in the derived profile",
        profile.critical_tool_names == ("execute_approved_action",),
    )
    check(
        "FTF-1701",
        "max_side_effect_class reflects the highest-ranked real spec (irreversible_write)",
        profile.max_side_effect_class
        == EXECUTE_APPROVED_ACTION.side_effect_class
        == "irreversible_write",
    )
    check(
        "FTF-1701",
        "negative_acceptance: qualifies_this_reference_workload_only is always True",
        profile.qualifies_this_reference_workload_only is True,
    )
    check(
        "FTF-1701",
        "an explicit language profile is declared",
        profile.supported_languages == ("en",),
    )


def _seeded_server() -> tuple[ReferenceBusinessServer, str]:
    server = ReferenceBusinessServer()
    record = server.seed_record(tenant_id="tenant-a", owner_subject_id="alice", title="Q3 renewal")
    return server, record.record_id


def test_cross_tenant_get_record_denied() -> None:
    server, record_id = _seeded_server()
    check(
        "FTF-1701",
        "cross-tenant get_record is refused with ReferenceAuthorizationError",
        _raises(
            ReferenceAuthorizationError,
            server.get_record,
            tenant_id="tenant-b",
            subject_id="alice",
            record_id=record_id,
        ),
    )


def test_cross_subject_idor_get_record_denied() -> None:
    server, record_id = _seeded_server()
    check(
        "FTF-1701",
        "cross-subject (IDOR) get_record within the same tenant is refused",
        _raises(
            ReferenceAuthorizationError,
            server.get_record,
            tenant_id="tenant-a",
            subject_id="mallory",
            record_id=record_id,
        ),
    )


def test_owner_get_record_succeeds() -> None:
    server, record_id = _seeded_server()
    result = server.get_record(tenant_id="tenant-a", subject_id="alice", record_id=record_id)
    check(
        "FTF-1701", "the owning subject can read their own record", result["record_id"] == record_id
    )
    check(
        "FTF-1701",
        "a freshly seeded record starts open with no notes",
        result["status"] == "open" and result["notes"] == [],
    )


def test_create_note_idempotency() -> None:
    server, record_id = _seeded_server()
    key = "note-key-1"
    first = server.create_note(
        tenant_id="tenant-a",
        subject_id="alice",
        record_id=record_id,
        text="called twice",
        idempotency_key=key,
    )
    second = server.create_note(
        tenant_id="tenant-a",
        subject_id="alice",
        record_id=record_id,
        text="called twice",
        idempotency_key=key,
    )
    check(
        "FTF-1701",
        "repeating create_note with the same idempotency_key returns the same note",
        first == second,
    )
    record_view = server.get_record(tenant_id="tenant-a", subject_id="alice", record_id=record_id)
    check(
        "FTF-1701",
        "exactly one note exists after two idempotent create_note calls, never two",
        len(record_view["notes"]) == 1,
    )


def test_execute_denied_while_pending() -> None:
    server, record_id = _seeded_server()
    approval = server.request_approval(
        tenant_id="tenant-a", subject_id="alice", record_id=record_id, action="archive_record"
    )
    check(
        "FTF-1701",
        "negative_acceptance: execute on a pending approval is refused (denial, tested directly)",
        _raises(
            ReferenceDenialError,
            server.execute_approved_action,
            tenant_id="tenant-a",
            subject_id="alice",
            approval_id=approval["approval_id"],
            idempotency_key="exec-1",
        ),
    )


def test_execute_denied_after_explicit_denial() -> None:
    server, record_id = _seeded_server()
    approval = server.request_approval(
        tenant_id="tenant-a", subject_id="alice", record_id=record_id, action="archive_record"
    )
    server.decide_approval(
        tenant_id="tenant-a",
        approver_subject_id="approver-1",
        approval_id=approval["approval_id"],
        decision="denied",
    )
    check(
        "FTF-1701",
        "negative_acceptance: execute_approved_action on a denied approval is refused",
        _raises(
            ReferenceDenialError,
            server.execute_approved_action,
            tenant_id="tenant-a",
            subject_id="alice",
            approval_id=approval["approval_id"],
            idempotency_key="exec-2",
        ),
    )


def test_rollback_revoked_approval_denies_execution() -> None:
    server, record_id = _seeded_server()
    approval = server.request_approval(
        tenant_id="tenant-a", subject_id="alice", record_id=record_id, action="archive_record"
    )
    server.decide_approval(
        tenant_id="tenant-a",
        approver_subject_id="approver-1",
        approval_id=approval["approval_id"],
        decision="approved",
    )
    server.revoke_approval(
        tenant_id="tenant-a", approver_subject_id="approver-1", approval_id=approval["approval_id"]
    )
    check(
        "FTF-1701",
        "rollback state, tested directly: a revoked (previously approved) grant refuses execution",
        _raises(
            ReferenceDenialError,
            server.execute_approved_action,
            tenant_id="tenant-a",
            subject_id="alice",
            approval_id=approval["approval_id"],
            idempotency_key="exec-3",
        ),
    )


def test_execute_only_by_original_requester() -> None:
    server, record_id = _seeded_server()
    approval = server.request_approval(
        tenant_id="tenant-a", subject_id="alice", record_id=record_id, action="archive_record"
    )
    server.decide_approval(
        tenant_id="tenant-a",
        approver_subject_id="approver-1",
        approval_id=approval["approval_id"],
        decision="approved",
    )
    check(
        "FTF-1701",
        "only the original requester may execute their own approved action",
        _raises(
            ReferenceAuthorizationError,
            server.execute_approved_action,
            tenant_id="tenant-a",
            subject_id="mallory",
            approval_id=approval["approval_id"],
            idempotency_key="exec-4",
        ),
    )


def test_execute_approved_action_succeeds_and_is_idempotent() -> None:
    server, record_id = _seeded_server()
    approval = server.request_approval(
        tenant_id="tenant-a", subject_id="alice", record_id=record_id, action="archive_record"
    )
    server.decide_approval(
        tenant_id="tenant-a",
        approver_subject_id="approver-1",
        approval_id=approval["approval_id"],
        decision="approved",
    )
    key = "exec-key-1"
    first = server.execute_approved_action(
        tenant_id="tenant-a",
        subject_id="alice",
        approval_id=approval["approval_id"],
        idempotency_key=key,
    )
    second = server.execute_approved_action(
        tenant_id="tenant-a",
        subject_id="alice",
        approval_id=approval["approval_id"],
        idempotency_key=key,
    )
    check(
        "FTF-1701",
        "a genuinely approved action executes and returns status=executed",
        first["status"] == "executed",
    )
    check(
        "FTF-1701",
        "repeating execute_approved_action with the same idempotency_key never re-executes",
        first == second,
    )
    record_view = server.get_record(tenant_id="tenant-a", subject_id="alice", record_id=record_id)
    check(
        "FTF-1701",
        "the record status reflects the executed action exactly once",
        record_view["status"] == "archived",
    )

    check(
        "FTF-1701",
        "re-execution with a DIFFERENT idempotency_key is still refused (already executed)",
        _raises(
            ReferenceDenialError,
            server.execute_approved_action,
            tenant_id="tenant-a",
            subject_id="alice",
            approval_id=approval["approval_id"],
            idempotency_key="a-different-key",
        ),
    )


def test_clients_cannot_fabricate_grants_via_the_tool_call_boundary() -> None:
    server, record_id = _seeded_server()
    approval = server.request_approval(
        tenant_id="tenant-a", subject_id="alice", record_id=record_id, action="archive_record"
    )
    check(
        "FTF-1701",
        "decide_approval is not dispatchable -- the grant surface is unreachable via tool calls",
        _raises(
            ReferenceAgentSpecificationError,
            dispatch_tool_call,
            server,
            "decide_approval",
            tenant_id="tenant-a",
            subject_id="alice",
            arguments={"approval_id": approval["approval_id"], "decision": "approved"},
        ),
    )
    check(
        "FTF-1701",
        "revoke_approval is likewise not a dispatchable tool",
        _raises(
            ReferenceAgentSpecificationError,
            dispatch_tool_call,
            server,
            "revoke_approval",
            tenant_id="tenant-a",
            subject_id="alice",
            arguments={"approval_id": approval["approval_id"]},
        ),
    )


def test_dispatch_rejects_malformed_arguments_before_the_handler_runs() -> None:
    server, record_id = _seeded_server()
    check(
        "FTF-1701",
        "dispatch_tool_call refuses a missing required argument before the handler runs",
        _raises(
            ReferenceAgentSpecificationError,
            dispatch_tool_call,
            server,
            "get_record",
            tenant_id="tenant-a",
            subject_id="alice",
            arguments={},
        ),
    )
    check(
        "FTF-1701",
        "dispatch_tool_call refuses an excess/unknown argument (additionalProperties: false)",
        _raises(
            ReferenceAgentSpecificationError,
            dispatch_tool_call,
            server,
            "get_record",
            tenant_id="tenant-a",
            subject_id="alice",
            arguments={"record_id": record_id, "unexpected_field": "smuggled"},
        ),
    )


def test_dispatch_rejects_an_out_of_contract_handler_result() -> None:
    server, record_id = _seeded_server()

    def _broken_get_record(*, tenant_id: str, subject_id: str, record_id: str) -> dict[str, object]:
        return {"record_id": record_id}  # missing title/status/notes -- violates output_schema_json

    server.get_record = _broken_get_record  # type: ignore[method-assign]
    check(
        "FTF-1701",
        "dispatch_tool_call refuses an out-of-contract handler result (FTF-0405 validate_instance)",
        _raises(
            ReferenceAgentSpecificationError,
            dispatch_tool_call,
            server,
            "get_record",
            tenant_id="tenant-a",
            subject_id="alice",
            arguments={"record_id": record_id},
        ),
    )


def test_full_happy_path_through_the_real_dispatcher() -> None:
    server = ReferenceBusinessServer()
    record = server.seed_record(
        tenant_id="tenant-a", owner_subject_id="alice", title="Renewal notice"
    )

    search = dispatch_tool_call(
        server,
        "search_records",
        tenant_id="tenant-a",
        subject_id="alice",
        arguments={"query": "renewal"},
    )
    check(
        "FTF-1701",
        "search_records finds the seeded record through the real dispatcher",
        len(search["records"]) == 1,
    )

    fetched = dispatch_tool_call(
        server,
        "get_record",
        tenant_id="tenant-a",
        subject_id="alice",
        arguments={"record_id": record.record_id},
    )
    check(
        "FTF-1701",
        "get_record returns the seeded record through the real dispatcher",
        fetched["record_id"] == record.record_id,
    )

    noted = dispatch_tool_call(
        server,
        "create_note",
        tenant_id="tenant-a",
        subject_id="alice",
        arguments={
            "record_id": record.record_id,
            "text": "reviewed",
            "idempotency_key": "happy-note-1",
        },
    )
    check(
        "FTF-1701",
        "create_note returns a real note_id through the real dispatcher",
        bool(noted["note_id"]),
    )

    requested = dispatch_tool_call(
        server,
        "request_approval",
        tenant_id="tenant-a",
        subject_id="alice",
        arguments={"record_id": record.record_id, "action": "archive_record"},
    )
    check(
        "FTF-1701",
        "request_approval starts pending through the real dispatcher",
        requested["status"] == "pending",
    )

    server.decide_approval(
        tenant_id="tenant-a",
        approver_subject_id="approver-1",
        approval_id=requested["approval_id"],
        decision="approved",
    )

    executed = dispatch_tool_call(
        server,
        "execute_approved_action",
        tenant_id="tenant-a",
        subject_id="alice",
        arguments={"approval_id": requested["approval_id"], "idempotency_key": "happy-exec-1"},
    )
    check(
        "FTF-1701",
        "the full five-tool reference workflow executes end to end through the real dispatcher",
        executed["status"] == "executed",
    )


# -- Acceptance clause 1: frozen shared contract files ----------------------


def _load_jsonschema() -> dict:
    return json.loads(JSONSCHEMA_PATH.read_text(encoding="utf-8"))


def _load_openapi() -> dict:
    return yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))


def _validate_against_schema(instance: dict, schema: dict, defs: dict) -> list[str]:
    """A tiny, stdlib-only structural validator -- deliberately not the
    third-party `jsonschema` package (same reasoning as FTF-1601's own test
    suite: adding it would itself be a second, unapproved new-runtime-
    dependency ask-first item). Checks exactly the constraint kinds this
    contract actually uses."""
    errors: list[str] = []
    props = schema.get("properties", {})
    required = schema.get("required", [])
    for field_name in required:
        if field_name not in instance:
            errors.append(f"{field_name}: required but missing")
    for field_name, field_schema in props.items():
        if field_name not in instance:
            continue
        value = instance[field_name]
        expected_type = field_schema.get("type")
        if expected_type == "string" and not isinstance(value, str):
            errors.append(f"{field_name}: expected string, got {type(value).__name__}")
            continue
        if expected_type == "boolean" and not isinstance(value, bool):
            errors.append(f"{field_name}: expected boolean, got {type(value).__name__}")
            continue
        if expected_type == "array" and not isinstance(value, list | tuple):
            errors.append(f"{field_name}: expected array, got {type(value).__name__}")
            continue
        enum = field_schema.get("enum")
        if enum is not None and value not in enum:
            errors.append(f"{field_name}: {value!r} not in enum {enum}")
        const = field_schema.get("const")
        if const is not None and value != const:
            errors.append(f"{field_name}: {value!r} does not equal const {const!r}")
    return errors


def test_openapi_contract_has_no_placeholder_endpoints() -> None:
    doc = _load_openapi()
    check(
        "FTF-1701",
        "17-interface.yaml parses as valid OpenAPI YAML with an openapi version",
        isinstance(doc.get("openapi"), str) and doc["openapi"].startswith("3."),
    )
    check(
        "FTF-1701",
        "negative_acceptance / Law 11: 17-interface.yaml declares zero paths -- "
        "this task owns no live route",
        doc.get("paths") == {},
    )
    schemas = doc.get("components", {}).get("schemas", {})
    for entity in ("ReferenceToolSpec", "ReferenceAgentProfile", "ApprovalRequestRecord"):
        check(
            "FTF-1701",
            f"17-interface.yaml's components.schemas defines {entity}",
            entity in schemas and schemas[entity].get("type") == "object",
        )


def test_real_tool_spec_validates_against_the_frozen_contract() -> None:
    schema_doc = _load_jsonschema()
    defs = schema_doc["$defs"]
    instance = dataclasses.asdict(EXECUTE_APPROVED_ACTION)
    errors = _validate_against_schema(instance, defs["ReferenceToolSpec"], defs)
    check(
        "FTF-1701",
        f"a real ReferenceToolSpec satisfies the frozen contract schema (errors: {errors})",
        errors == [],
    )

    bad_instance = {**instance, "side_effect_class": "not_a_real_class"}
    errors = _validate_against_schema(bad_instance, defs["ReferenceToolSpec"], defs)
    check(
        "FTF-1701",
        "the same validator genuinely rejects an unsupported side_effect_class "
        "(not a decorative always-pass check)",
        errors != [],
    )


def test_real_profile_validates_against_the_frozen_contract() -> None:
    schema_doc = _load_jsonschema()
    defs = schema_doc["$defs"]
    profile = build_reference_agent_profile()
    instance = dataclasses.asdict(profile)
    errors = _validate_against_schema(instance, defs["ReferenceAgentProfile"], defs)
    check(
        "FTF-1701",
        f"the real derived ReferenceAgentProfile satisfies the frozen contract (errors: {errors})",
        errors == [],
    )

    bad_instance = {**instance, "qualifies_this_reference_workload_only": False}
    errors = _validate_against_schema(bad_instance, defs["ReferenceAgentProfile"], defs)
    check(
        "FTF-1701",
        "the validator genuinely rejects qualifies_this_reference_workload_only=False "
        "against the frozen const:true contract",
        errors != [],
    )


def test_real_approval_record_validates_against_the_frozen_contract() -> None:
    server, record_id = _seeded_server()
    approval = server.request_approval(
        tenant_id="tenant-a", subject_id="alice", record_id=record_id, action="archive_record"
    )
    schema_doc = _load_jsonschema()
    defs = schema_doc["$defs"]
    instance = {
        "approval_id": approval["approval_id"],
        "record_id": record_id,
        "action": "archive_record",
        "status": approval["status"],
        "requested_by_subject_id": "alice",
    }
    errors = _validate_against_schema(instance, defs["ApprovalRequestRecord"], defs)
    check(
        "FTF-1701",
        f"a real ApprovalRequestRecord satisfies the frozen contract schema (errors: {errors})",
        errors == [],
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-1701 Reference agent specification tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1701 Reference agent specification tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
